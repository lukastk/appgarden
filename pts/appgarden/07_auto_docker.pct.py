# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp auto_docker

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Auto-Docker
#
# Detect project runtime from source files and generate a Dockerfile.

# %%
#|export
from dataclasses import dataclass
from pathlib import Path

from appgarden.routing import render_template

# %% [markdown]
# ## Runtime detection

# %%
#|export
@dataclass
class Runtime:
    name: str
    base_image: str
    setup_cmd: str
    copy_first: str | None = None

# %%
#|export
RUNTIMES: list[tuple[str, Runtime]] = [
    ("package.json", Runtime(
        name="nodejs",
        base_image="node:22",
        setup_cmd="npm install",
        copy_first="package*.json",
    )),
    ("requirements.txt", Runtime(
        name="python-pip",
        base_image="python:3.12",
        setup_cmd="pip install -r requirements.txt",
        copy_first="requirements.txt",
    )),
    ("pyproject.toml", Runtime(
        name="python",
        base_image="python:3.12",
        setup_cmd="pip install .",
        copy_first="pyproject.toml",
    )),
    ("Gemfile", Runtime(
        name="ruby",
        base_image="ruby:3.3",
        setup_cmd="bundle install",
        copy_first="Gemfile*",
    )),
    ("go.mod", Runtime(
        name="go",
        base_image="golang:1.23",
        setup_cmd="go build -o /app/server .",
        copy_first="go.*",
    )),
    ("Cargo.toml", Runtime(
        name="rust",
        base_image="rust:1.83",
        setup_cmd="cargo build --release",
        copy_first="Cargo.*",
    )),
]

# %%
#|export
def detect_runtime(source_path: str | Path) -> Runtime | None:
    """Detect the project runtime from files in *source_path*.

    Returns the first matching ``Runtime``, or ``None`` if no match.
    """
    source = Path(source_path)
    for indicator, runtime in RUNTIMES:
        if (source / indicator).exists():
            return runtime
    return None

# %%
#|export
def infer_setup_command(runtime: Runtime) -> str:
    """Return the default setup/install command for a runtime."""
    return runtime.setup_cmd

# %% [markdown]
# ## Dockerfile generation

# %%
#|export
def generate_dockerfile(
    runtime: Runtime,
    container_port: int,
    cmd: str,
    setup_cmd: str | None = None,
) -> str:
    """Render a Dockerfile for the given runtime.

    Uses the ``Dockerfile.j2`` template.
    """
    return render_template(
        "Dockerfile.j2",
        base_image=runtime.base_image,
        copy_first=runtime.copy_first,
        setup_cmd=setup_cmd or runtime.setup_cmd,
        container_port=container_port,
        cmd=cmd,
    )

# %% [markdown]
# ## deploy_auto
#
# Auto-detect runtime, generate Dockerfile, then deploy as dockerfile method.

# %%
#|export
from appgarden.config import ServerConfig
from appgarden.remote import (
    APPGARDEN_ROOT, RemoteContext, make_remote_context,
    ssh_connect, run_remote_command, write_remote_file,
    read_garden_state,
)
from appgarden.deploy import (
    upload_source, _app_dir, _source_dir, _write_env_file,
    _deploy_systemd_unit, _register_app, is_git_url,
)
from appgarden.ports import allocate_port
from appgarden.routing import parse_url, deploy_caddy_config

from rich.console import Console
console = Console()

# %%
#|export
def deploy_auto(
    server: ServerConfig,
    name: str,
    source: str,
    cmd: str,
    url: str,
    port: int | None = None,
    container_port: int = 3000,
    setup_cmd: str | None = None,
    branch: str | None = None,
    env_vars: dict[str, str] | None = None,
    env_file: str | None = None,
) -> None:
    """Auto-detect runtime, generate Dockerfile, build and deploy."""
    ctx = make_remote_context(server)
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying auto app[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        # Upload source
        console.print("  [dim]Uploading source...[/dim]")
        source_type = upload_source(server, host, name, source, branch, ctx=ctx)
        source_path = _source_dir(name, ctx)
        adir = _app_dir(name, ctx)

        # Detect runtime from local source (if local) or remote
        runtime = None
        if not is_git_url(source):
            runtime = detect_runtime(source)
        if runtime is None:
            # Try detecting from remote
            for indicator, rt in RUNTIMES:
                try:
                    run_remote_command(host, f"test -f {source_path}/{indicator}")
                    runtime = rt
                    break
                except RuntimeError:
                    continue

        if runtime is None:
            raise RuntimeError(
                f"Could not detect runtime for '{name}'. "
                "Provide a Dockerfile or use --method dockerfile."
            )

        console.print(f"  [dim]Detected runtime: {runtime.name}[/dim]")

        # Allocate port
        if port is None:
            port = allocate_port(host, name)
        console.print(f"  [dim]Port: {port}[/dim]")

        # Generate and write Dockerfile
        dockerfile_content = generate_dockerfile(
            runtime, container_port, cmd,
            setup_cmd=setup_cmd,
        )
        write_remote_file(host, f"{source_path}/Dockerfile", dockerfile_content)

        # Build Docker image
        image_name = f"appgarden-{name}"
        console.print("  [dim]Building Docker image...[/dim]")
        run_remote_command(
            host,
            f"docker build -t {image_name} {source_path}",
            timeout=600,
        )

        # Write .env file
        env_path = _write_env_file(host, name, env_vars, env_file, ctx=ctx)

        # Generate docker-compose.yml
        compose_content = render_template(
            "docker-compose.yml.j2",
            port=port,
            container_port=container_port,
            env_file=".env" if env_path else None,
            volumes=None,
        )
        compose_content = compose_content.replace(
            "    build: .",
            f"    image: {image_name}",
        )
        write_remote_file(host, f"{adir}/docker-compose.yml", compose_content)

        # Create systemd unit
        console.print("  [dim]Creating systemd service...[/dim]")
        unit_content = render_template(
            "systemd.service.j2",
            name=name,
            method="docker-compose",
            working_dir=adir,
            env_file=None,
            env_vars={},
            exec_start="/usr/bin/docker compose up",
            exec_stop="/usr/bin/docker compose down",
        )
        unit_name = _deploy_systemd_unit(host, name, unit_content, ctx=ctx)

        # Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host, ctx=ctx)
        deploy_caddy_config(
            host, app_name=name, domain=domain, port=port, path=path,
            garden_state=garden_state, ctx=ctx,
        )

        # Register
        _register_app(
            host, garden_state, name, "auto", url,
            source=source, source_type=source_type,
            port=port, container_port=container_port,
            branch=branch, systemd_unit=unit_name,
            extra={"auto_detected_runtime": runtime.name}, ctx=ctx,
        )

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")
