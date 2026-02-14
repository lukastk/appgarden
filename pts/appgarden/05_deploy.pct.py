# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp deploy

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Deployment Logic
#
# Orchestrates deploying applications to remote servers.
# Supports static, docker-compose, dockerfile, command, and auto methods.

# %%
#|export
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from appgarden.config import ServerConfig
from appgarden.remote import (
    APPGARDEN_ROOT,
    ssh_connect, run_remote_command, write_remote_file,
    read_garden_state, write_garden_state, upload_directory,
)
from appgarden.ports import allocate_port
from appgarden.routing import parse_url, deploy_caddy_config, render_template

console = Console()

# %% [markdown]
# ## Source detection
#
# Determine whether a `--source` value is a local path or a git URL.

# %%
#|export
def is_git_url(source: str) -> bool:
    """Return True if *source* looks like a git URL."""
    return (
        source.startswith("https://")
        or source.startswith("http://")
        or source.startswith("git@")
        or source.startswith("git://")
        or source.endswith(".git")
    )

# %% [markdown]
# ## Source upload / clone

# %%
#|export
def _app_dir(name: str) -> str:
    """Return the remote app directory."""
    return f"{APPGARDEN_ROOT}/apps/{name}"

# %%
#|export
def _source_dir(name: str) -> str:
    """Return the remote source directory for an app."""
    return f"{_app_dir(name)}/source"

# %%
#|export
def upload_source(
    server: ServerConfig,
    host,
    name: str,
    source: str,
    branch: str | None = None,
) -> str:
    """Upload or clone source code to the remote server.

    Returns ``"local"`` or ``"git"`` indicating the source type.
    """
    remote_source = _source_dir(name)
    run_remote_command(host, f"mkdir -p {remote_source}")

    if is_git_url(source):
        branch_flag = f"-b {branch}" if branch else ""
        run_remote_command(
            host,
            f"rm -rf {remote_source} && git clone {branch_flag} {source} {remote_source}",
            timeout=120,
        )
        return "git"
    else:
        upload_directory(server, source, remote_source)
        return "local"

# %% [markdown]
# ## deploy_static
#
# Deploy a static site served directly by Caddy.
# No systemd service needed — Caddy serves the files.

# %%
#|export
def deploy_static(
    server: ServerConfig,
    name: str,
    source: str,
    url: str,
    branch: str | None = None,
) -> None:
    """Deploy a static site to the remote server.

    1. Upload/clone source
    2. Deploy Caddy config (file_server)
    3. Register in garden.json
    """
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying static site[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        # 1. Upload source
        console.print("  [dim]Uploading source...[/dim]")
        source_type = upload_source(server, host, name, source, branch)
        source_path = _source_dir(name)

        # 2. Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host)
        deploy_caddy_config(
            host, app_name=name, domain=domain, path=path,
            method="static", source_path=source_path,
            garden_state=garden_state,
        )

        # 3. Register
        _register_app(
            host, garden_state, name, "static", url,
            source=source, source_type=source_type, branch=branch,
        )

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")

# %% [markdown]
# ## Environment file helpers

# %%
#|export
def _write_env_file(
    host,
    name: str,
    env_vars: dict[str, str] | None = None,
    env_file: str | None = None,
) -> str | None:
    """Write a .env file for an app. Returns the remote path, or None."""
    if not env_vars and not env_file:
        return None

    content = ""
    if env_file:
        content = Path(env_file).read_text()
    if env_vars:
        for k, v in env_vars.items():
            content += f"{k}={v}\n"

    remote_path = f"{_app_dir(name)}/.env"
    write_remote_file(host, remote_path, content)
    run_remote_command(host, f"chmod 600 {remote_path}")
    return remote_path

# %% [markdown]
# ## Systemd helpers

# %%
#|export
SYSTEMD_UNIT_DIR = "/etc/systemd/system"

def _systemd_unit_name(name: str) -> str:
    """Return the systemd unit name for an app."""
    return f"appgarden-{name}.service"

# %%
#|export
def _deploy_systemd_unit(host, name: str, unit_content: str) -> str:
    """Write a systemd unit file, reload daemon, enable and start."""
    unit_name = _systemd_unit_name(name)
    unit_path = f"{SYSTEMD_UNIT_DIR}/{unit_name}"
    write_remote_file(host, unit_path, unit_content)
    run_remote_command(host, "systemctl daemon-reload")
    run_remote_command(host, f"systemctl enable {unit_name}")
    run_remote_command(host, f"systemctl restart {unit_name}")
    return unit_name

# %% [markdown]
# ## _register_app
#
# Common logic for registering an app in garden.json.

# %%
#|export
def _register_app(
    host,
    garden_state: dict,
    name: str,
    method: str,
    url: str,
    source: str | None,
    source_type: str | None,
    port: int | None = None,
    container_port: int | None = None,
    branch: str | None = None,
    systemd_unit: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Register an app in garden.json and write app.json. Returns the app entry."""
    domain, path = parse_url(url)
    now = datetime.now(timezone.utc).isoformat()
    app_entry = {
        "name": name,
        "method": method,
        "url": url,
        "routing": "subdirectory" if path else "subdomain",
        "created_at": now,
        "updated_at": now,
    }
    if port is not None:
        app_entry["port"] = port
    if container_port is not None:
        app_entry["container_port"] = container_port
    if source is not None:
        app_entry["source"] = source
        app_entry["source_type"] = source_type
        app_entry["source_path"] = _source_dir(name)
    if branch:
        app_entry["branch"] = branch
    if systemd_unit:
        app_entry["systemd_unit"] = systemd_unit
    if extra:
        app_entry.update(extra)

    garden_state["apps"][name] = app_entry
    write_garden_state(host, garden_state)

    app_json_path = f"{_app_dir(name)}/app.json"
    write_remote_file(host, app_json_path, json.dumps(app_entry, indent=2))
    return app_entry

# %% [markdown]
# ## deploy_command
#
# Bare-process deployment managed by systemd (no Docker).

# %%
#|export
def deploy_command(
    server: ServerConfig,
    name: str,
    cmd: str,
    url: str,
    port: int | None = None,
    source: str | None = None,
    branch: str | None = None,
    env_vars: dict[str, str] | None = None,
    env_file: str | None = None,
) -> None:
    """Deploy a bare-process app managed by systemd."""
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying command app[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        run_remote_command(host, f"mkdir -p {_app_dir(name)}")

        # Upload source if provided
        source_type = None
        if source:
            console.print("  [dim]Uploading source...[/dim]")
            source_type = upload_source(server, host, name, source, branch)

        # Allocate port
        if port is None:
            port = allocate_port(host, name)
        console.print(f"  [dim]Port: {port}[/dim]")

        # Write .env file
        env_path = _write_env_file(host, name, env_vars, env_file)

        # Create systemd unit
        console.print("  [dim]Creating systemd service...[/dim]")
        service_env = {"PORT": str(port)}
        if env_vars:
            service_env.update(env_vars)

        unit_content = render_template(
            "systemd.service.j2",
            name=name,
            method="command",
            working_dir=_source_dir(name) if source else _app_dir(name),
            env_file=env_path,
            env_vars=service_env,
            exec_start=cmd,
            exec_stop=None,
        )
        unit_name = _deploy_systemd_unit(host, name, unit_content)

        # Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host)
        deploy_caddy_config(
            host, app_name=name, domain=domain, port=port, path=path,
            garden_state=garden_state,
        )

        # Register
        _register_app(
            host, garden_state, name, "command", url,
            source=source, source_type=source_type,
            port=port, branch=branch, systemd_unit=unit_name,
        )

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")

# %% [markdown]
# ## deploy_docker_compose
#
# Deploy using a user-provided docker-compose.yml.

# %%
#|export
def deploy_docker_compose(
    server: ServerConfig,
    name: str,
    source: str,
    url: str,
    port: int | None = None,
    branch: str | None = None,
    env_vars: dict[str, str] | None = None,
    env_file: str | None = None,
) -> None:
    """Deploy a docker-compose app."""
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying docker-compose app[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        # Upload source (docker-compose.yml lives in the app dir root)
        console.print("  [dim]Uploading source...[/dim]")
        app_dir = _app_dir(name)
        run_remote_command(host, f"mkdir -p {app_dir}")

        if is_git_url(source):
            branch_flag = f"-b {branch}" if branch else ""
            run_remote_command(
                host,
                f"rm -rf {app_dir}/source && git clone {branch_flag} {source} {app_dir}/source",
                timeout=120,
            )
            source_type = "git"
            working_dir = f"{app_dir}/source"
        else:
            upload_directory(server, source, app_dir)
            source_type = "local"
            working_dir = app_dir

        # Allocate/register port
        if port is None:
            port = allocate_port(host, name)
        console.print(f"  [dim]Port: {port}[/dim]")

        # Write .env file
        env_path = _write_env_file(host, name, env_vars, env_file)

        # Create systemd unit
        console.print("  [dim]Creating systemd service...[/dim]")
        unit_content = render_template(
            "systemd.service.j2",
            name=name,
            method="docker-compose",
            working_dir=working_dir,
            env_file=None,
            env_vars={},
            exec_start="/usr/bin/docker compose up",
            exec_stop="/usr/bin/docker compose down",
        )
        unit_name = _deploy_systemd_unit(host, name, unit_content)

        # Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host)
        deploy_caddy_config(
            host, app_name=name, domain=domain, port=port, path=path,
            garden_state=garden_state,
        )

        # Register
        _register_app(
            host, garden_state, name, "docker-compose", url,
            source=source, source_type=source_type,
            port=port, branch=branch, systemd_unit=unit_name,
        )

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")

# %% [markdown]
# ## deploy_dockerfile
#
# Deploy using a user-provided Dockerfile.
# Builds the image on the server, generates a docker-compose.yml wrapper.

# %%
#|export
def deploy_dockerfile(
    server: ServerConfig,
    name: str,
    source: str,
    url: str,
    port: int | None = None,
    container_port: int = 3000,
    branch: str | None = None,
    env_vars: dict[str, str] | None = None,
    env_file: str | None = None,
) -> None:
    """Deploy an app from a Dockerfile."""
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying dockerfile app[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        # Upload source
        console.print("  [dim]Uploading source...[/dim]")
        source_type = upload_source(server, host, name, source, branch)
        source_path = _source_dir(name)
        app_dir = _app_dir(name)

        # Allocate port
        if port is None:
            port = allocate_port(host, name)
        console.print(f"  [dim]Port: {port}[/dim]")

        # Build Docker image
        image_name = f"appgarden-{name}"
        console.print("  [dim]Building Docker image...[/dim]")
        run_remote_command(
            host,
            f"docker build -t {image_name} {source_path}",
            timeout=600,
        )

        # Write .env file
        env_path = _write_env_file(host, name, env_vars, env_file)

        # Generate docker-compose.yml
        compose_content = render_template(
            "docker-compose.yml.j2",
            port=port,
            container_port=container_port,
            env_file=".env" if env_path else None,
            volumes=None,
        )
        # Replace "build: ." with "image: <image>" since we pre-built
        compose_content = compose_content.replace(
            "    build: .",
            f"    image: {image_name}",
        )
        write_remote_file(host, f"{app_dir}/docker-compose.yml", compose_content)

        # Create systemd unit
        console.print("  [dim]Creating systemd service...[/dim]")
        unit_content = render_template(
            "systemd.service.j2",
            name=name,
            method="dockerfile",
            working_dir=app_dir,
            env_file=None,
            env_vars={},
            exec_start="/usr/bin/docker compose up",
            exec_stop="/usr/bin/docker compose down",
        )
        unit_name = _deploy_systemd_unit(host, name, unit_content)

        # Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host)
        deploy_caddy_config(
            host, app_name=name, domain=domain, port=port, path=path,
            garden_state=garden_state,
        )

        # Register
        _register_app(
            host, garden_state, name, "dockerfile", url,
            source=source, source_type=source_type,
            port=port, container_port=container_port,
            branch=branch, systemd_unit=unit_name,
        )

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")
