# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp cli

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %%
#|export
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from appgarden.config import (
    AppGardenConfig, ServerConfig,
    load_config, save_config, config_path, get_server,
)
from appgarden.server import init_server, ping_server, INIT_STEPS
from appgarden.deploy import deploy_static, deploy_command, deploy_docker_compose, deploy_dockerfile
from appgarden.auto_docker import deploy_auto
from appgarden.apps import (
    list_apps, list_apps_with_status, app_status,
    stop_app, start_app, restart_app,
    remove_app, redeploy_app, app_logs,
)
from appgarden.remote import (
    ssh_connect, make_remote_context,
    validate_app_name, validate_domain, validate_url_path, validate_branch, validate_env_key,
)
from appgarden.environments import load_project_config, resolve_environment, resolve_all_environments
from appgarden.tunnel import open_tunnel, close_tunnel, list_tunnels, cleanup_stale_tunnels

# %% [markdown]
# # CLI Application
#
# Typer-based CLI for deploying web applications to remote servers.

# %%
#|export
import socket

# %%
#|export
_verbose = False
_quiet = False

def _version_callback(value: bool):
    if value:
        from appgarden import __version__
        typer.echo(f"appgarden {__version__}")
        raise typer.Exit()

def _main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-essential output"),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show version"),
):
    global _verbose, _quiet
    _verbose = verbose
    _quiet = quiet

app = typer.Typer(
    name="appgarden",
    help="Deploy web applications to remote servers.",
    no_args_is_help=True,
    callback=_main_callback,
)
console = Console()

# %%
#|export
def _check_dns(url: str, expected_ip: str | None = None) -> None:
    """Warn if a URL's domain doesn't resolve or resolves to wrong IP."""
    if _quiet:
        return
    # Extract domain from URL
    domain = url.split("/")[0]
    try:
        resolved = socket.gethostbyname(domain)
        if expected_ip and resolved != expected_ip:
            console.print(
                f"[yellow]Warning:[/yellow] {domain} resolves to {resolved}, "
                f"expected {expected_ip}"
            )
        elif _verbose:
            console.print(f"[dim]DNS: {domain} -> {resolved}[/dim]")
    except socket.gaierror:
        console.print(
            f"[yellow]Warning:[/yellow] {domain} does not resolve. "
            f"Ensure DNS is configured before deploying."
        )

# %% [markdown]
# ## Version command

# %%
#|export
from appgarden import __version__

@app.command()
def version():
    """Show the appgarden version."""
    typer.echo(f"appgarden {__version__}")

# %% [markdown]
# ## Server subcommand group

# %%
#|export
server_app = typer.Typer(
    name="server",
    help="Manage servers.",
    no_args_is_help=True,
)
app.add_typer(server_app, name="server")

# %% [markdown]
# ### server add

# %%
#|export
@server_app.command("add")
def server_add(
    name: str = typer.Argument(help="Name for this server"),
    host: Optional[str] = typer.Option(None, help="Server IP or hostname"),
    hcloud_name: Optional[str] = typer.Option(None, help="Hetzner Cloud server name"),
    hcloud_context: Optional[str] = typer.Option(None, help="Hetzner Cloud CLI context"),
    ssh_user: str = typer.Option("root", help="SSH user"),
    ssh_key: str = typer.Option("~/.ssh/id_rsa", help="Path to SSH private key"),
    domain: str = typer.Option(..., help="Base domain for applications"),
    app_root: Optional[str] = typer.Option(None, "--app-root", help="App root directory on server (default: /srv/appgarden)"),
):
    """Add a server to the configuration."""
    validate_domain(domain)
    if not host and not (hcloud_name and hcloud_context):
        console.print("[red]Error:[/red] Provide either --host or both --hcloud-name and --hcloud-context")
        raise typer.Exit(code=1)

    cfg = load_config()
    cfg.servers[name] = ServerConfig(
        ssh_user=ssh_user,
        ssh_key=ssh_key,
        domain=domain,
        host=host,
        hcloud_name=hcloud_name,
        hcloud_context=hcloud_context,
        app_root=app_root,
    )
    if cfg.default_server is None:
        cfg.default_server = name
    save_config(cfg)
    console.print(f"Server [bold]{name}[/bold] added.")

# %% [markdown]
# ### server list

# %%
#|export
@server_app.command("list")
def server_list():
    """List configured servers."""
    cfg = load_config()
    if not cfg.servers:
        console.print("No servers configured.")
        raise typer.Exit()

    table = Table()
    table.add_column("Name")
    table.add_column("Host / hcloud")
    table.add_column("Domain")
    table.add_column("Default")

    for name, srv in cfg.servers.items():
        host_col = srv.host or f"hcloud:{srv.hcloud_name}"
        default_marker = "*" if name == cfg.default_server else ""
        table.add_row(name, host_col, srv.domain, default_marker)

    console.print(table)

# %% [markdown]
# ### server remove

# %%
#|export
@server_app.command("remove")
def server_remove(
    name: str = typer.Argument(help="Name of the server to remove"),
):
    """Remove a server from the configuration."""
    cfg = load_config()
    if name not in cfg.servers:
        console.print(f"[red]Error:[/red] Server '{name}' not found.")
        raise typer.Exit(code=1)

    del cfg.servers[name]
    if cfg.default_server == name:
        cfg.default_server = next(iter(cfg.servers), None)
    save_config(cfg)
    console.print(f"Server [bold]{name}[/bold] removed.")

# %% [markdown]
# ### server default

# %%
#|export
@server_app.command("default")
def server_default(
    name: str = typer.Argument(help="Name of the server to set as default"),
):
    """Set the default server."""
    cfg = load_config()
    if name not in cfg.servers:
        console.print(f"[red]Error:[/red] Server '{name}' not found.")
        raise typer.Exit(code=1)

    cfg.default_server = name
    save_config(cfg)
    console.print(f"Default server set to [bold]{name}[/bold].")

# %% [markdown]
# ### server init

# %%
#|export
@server_app.command("init")
def server_init_cmd(
    name: Optional[str] = typer.Argument(None, help="Server name (uses default if omitted)"),
    skip: Optional[list[str]] = typer.Option(None, "--skip", help="Skip optional steps (update, docker, caddy, firewall, ssh, fail2ban, upgrades)"),
    minimal: bool = typer.Option(False, "--minimal", help="Only run essential steps (skip all optional)"),
):
    """Initialise a server for AppGarden (installs Docker, Caddy, etc.)."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    # Build merged skip set: CLI --skip > --minimal > config init.skip
    if minimal:
        merged_skip = set(INIT_STEPS)
    else:
        merged_skip = set(srv.init.skip)
        if skip:
            merged_skip |= set(skip)

    # Validate skip values
    invalid = merged_skip - INIT_STEPS
    if invalid:
        console.print(f"[red]Error:[/red] Unknown init step(s): {', '.join(sorted(invalid))}. "
                       f"Valid steps: {', '.join(sorted(INIT_STEPS))}")
        raise typer.Exit(code=1)

    init_server(srv, skip=merged_skip)

# %% [markdown]
# ### server ping

# %%
#|export
@server_app.command("ping")
def server_ping_cmd(
    name: Optional[str] = typer.Argument(None, help="Server name (uses default if omitted)"),
):
    """Test SSH connectivity to a server."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    if ping_server(srv):
        console.print(f"[green]Server '{sname}' is reachable.[/green]")
    else:
        console.print(f"[red]Server '{sname}' is not reachable.[/red]")
        raise typer.Exit(code=1)

# %% [markdown]
# ## Deploy command

# %%
#|export
def _parse_env_list(env: list[str] | None) -> dict[str, str] | None:
    """Parse a list of KEY=VALUE strings into a dict."""
    if not env:
        return None
    result = {}
    for item in env:
        if "=" not in item:
            raise typer.BadParameter(f"Invalid env format: '{item}' (expected KEY=VALUE)")
        k, v = item.split("=", 1)
        validate_env_key(k)
        result[k] = v
    return result

# %%
#|export
DEPLOY_DEFAULTS = {"method": "static", "container_port": 3000}

def _resolve_deploy_params(
    cli: dict,
    env_cfg: dict | None = None,
    project_defaults: dict | None = None,
    global_defaults: dict | None = None,
) -> dict:
    """Layer: CLI > env > project > global > hardcoded."""
    result = dict(DEPLOY_DEFAULTS)
    for layer in [global_defaults, project_defaults, env_cfg, cli]:
        if layer:
            result.update({k: v for k, v in layer.items() if v is not None})
    return result

# %%
#|export
def _dispatch_deploy(
    srv: ServerConfig, name: str, method: str, url: str,
    source: str | None = None, port: int | None = None,
    container_port: int = 3000, cmd: str | None = None,
    setup_cmd: str | None = None, branch: str | None = None,
    env_vars: dict[str, str] | None = None, env_file: str | None = None,
) -> None:
    """Dispatch to the appropriate deploy function based on method."""
    if method == "static":
        if not source:
            console.print("[red]Error:[/red] --source is required for static deployments")
            raise typer.Exit(code=1)
        deploy_static(srv, name, source, url, branch=branch)

    elif method == "command":
        if not cmd:
            console.print("[red]Error:[/red] --cmd is required for command deployments")
            raise typer.Exit(code=1)
        deploy_command(srv, name, cmd, url, port=port, source=source,
                       branch=branch, env_vars=env_vars, env_file=env_file)

    elif method == "docker-compose":
        if not source:
            console.print("[red]Error:[/red] --source is required for docker-compose deployments")
            raise typer.Exit(code=1)
        deploy_docker_compose(srv, name, source, url, port=port,
                              branch=branch, env_vars=env_vars, env_file=env_file)

    elif method == "dockerfile":
        if not source:
            console.print("[red]Error:[/red] --source is required for dockerfile deployments")
            raise typer.Exit(code=1)
        deploy_dockerfile(srv, name, source, url, port=port,
                          container_port=container_port, branch=branch,
                          env_vars=env_vars, env_file=env_file)

    elif method == "auto":
        if not source:
            console.print("[red]Error:[/red] --source is required for auto deployments")
            raise typer.Exit(code=1)
        if not cmd:
            console.print("[red]Error:[/red] --cmd is required for auto deployments")
            raise typer.Exit(code=1)
        deploy_auto(srv, name, source, cmd, url, port=port,
                    container_port=container_port, setup_cmd=setup_cmd,
                    branch=branch, env_vars=env_vars, env_file=env_file)

    else:
        console.print(f"[red]Error:[/red] Unknown method '{method}'")
        raise typer.Exit(code=1)

# %%
#|export
def _env_config_to_dict(env_cfg: "EnvironmentConfig") -> dict:
    """Convert an EnvironmentConfig to a dict for cascading, dropping None/empty values."""
    d = {}
    for key in ("server", "method", "url", "source", "port", "container_port",
                "cmd", "setup_cmd", "branch", "env_file",
                "subdomain", "path", "domain"):
        val = getattr(env_cfg, key, None)
        if val is not None:
            d[key] = val
    if env_cfg.env:
        d["env"] = dict(env_cfg.env)
    return d

def _deploy_from_params(cfg: "AppGardenConfig", params: dict, app_name: str) -> None:
    """Execute a deploy from resolved cascaded params."""
    validate_app_name(app_name)

    server_name = params.get("server")
    try:
        sname, srv = get_server(cfg, server_name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    url = params.get("url")
    if not url:
        base_domain = params.get("domain") or srv.domain
        subdomain = params.get("subdomain")
        path_prefix = params.get("path")
        if subdomain:
            url = f"{subdomain}.{base_domain}"
        elif path_prefix:
            validate_url_path(path_prefix)
            url = f"{base_domain}/{path_prefix}"
    if not url:
        console.print("[red]Error:[/red] --url, --subdomain, or --path is required")
        raise typer.Exit(code=1)

    # Validate URL components
    from appgarden.routing import parse_url
    url_domain, url_path = parse_url(url)
    validate_domain(url_domain)
    if url_path:
        validate_url_path(url_path)

    # Validate branch if provided
    branch = params.get("branch")
    if branch:
        validate_branch(branch)

    method = params.get("method", "static")

    _check_dns(url, expected_ip=srv.host)
    console.print(f"Deploying [bold]{app_name}[/bold] to {url}...")
    _dispatch_deploy(
        srv, app_name, method, url,
        source=params.get("source"), port=params.get("port"),
        container_port=params.get("container_port", 3000),
        cmd=params.get("cmd"), setup_cmd=params.get("setup_cmd"),
        branch=params.get("branch"),
        env_vars=params.get("env"), env_file=params.get("env_file"),
    )

# %%
#|export
@app.command()
def deploy(
    name: str = typer.Argument(help="App name or environment name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
    method: Optional[str] = typer.Option(None, "--method", "-m", help="Deployment method (static, command, docker-compose, dockerfile, auto)"),
    source: Optional[str] = typer.Option(None, "--source", help="Source path or git URL"),
    url: Optional[str] = typer.Option(None, "--url", help="Full URL for the app (e.g. myapp.example.com)"),
    subdomain: Optional[str] = typer.Option(None, "--subdomain", help="Subdomain (combined with --domain or server domain)"),
    path: Optional[str] = typer.Option(None, "--path", help="Path prefix (combined with --domain or server domain)"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Base domain (overrides server domain for --subdomain/--path)"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Host port (auto-allocated if omitted)"),
    container_port: Optional[int] = typer.Option(None, "--container-port", help="Container port (for dockerfile/auto methods)"),
    cmd: Optional[str] = typer.Option(None, "--cmd", help="Start command (for command/auto methods)"),
    setup_cmd: Optional[str] = typer.Option(None, "--setup-cmd", help="Setup/install command (for auto method)"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Git branch (for git sources)"),
    env: Optional[list[str]] = typer.Option(None, "--env", help="Environment variable (KEY=VALUE, repeatable)"),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Path to .env file"),
    all_envs: bool = typer.Option(False, "--all-envs", help="Deploy all environments from appgarden.toml"),
):
    """Deploy an application to a remote server.

    If an appgarden.toml exists in the current directory, NAME can be an
    environment name (e.g. 'production', 'staging'). Use --all-envs to
    deploy all environments at once.
    """
    cfg = load_config()

    # Collect CLI flags (only non-None values participate in cascade)
    cli_flags = {
        "server": server, "method": method, "source": source,
        "url": url, "subdomain": subdomain, "path": path, "domain": domain,
        "port": port, "container_port": container_port,
        "cmd": cmd, "setup_cmd": setup_cmd, "branch": branch,
        "env_file": env_file,
    }
    env_vars = _parse_env_list(env)
    if env_vars:
        cli_flags["env"] = env_vars

    global_defaults = cfg.defaults or None

    # Try loading project config
    project = None
    try:
        project = load_project_config()
    except FileNotFoundError:
        pass

    project_defaults = project.app_defaults if project else None

    # --all-envs: deploy every environment with cascading
    if all_envs:
        if not project:
            console.print("[red]Error:[/red] No appgarden.toml found in current directory")
            raise typer.Exit(code=1)
        for env_name in sorted(project.environments.keys()):
            resolved_env = resolve_environment(project, env_name)
            env_overrides = _env_config_to_dict(resolved_env)
            params = _resolve_deploy_params(cli_flags, env_overrides, project_defaults, global_defaults)
            _deploy_from_params(cfg, params, resolved_env.app_name)
        return

    # Check if name matches an environment
    env_overrides = None
    app_name = name
    if project and name in project.environments:
        resolved_env = resolve_environment(project, name)
        env_overrides = _env_config_to_dict(resolved_env)
        app_name = resolved_env.app_name

    params = _resolve_deploy_params(cli_flags, env_overrides, project_defaults, global_defaults)

    _deploy_from_params(cfg, params, app_name)

# %% [markdown]
# ## Apps subcommand group

# %%
#|export
apps_app = typer.Typer(
    name="apps",
    help="Manage deployed applications.",
    no_args_is_help=True,
)
app.add_typer(apps_app, name="apps")

# %% [markdown]
# ### apps list

# %%
#|export
@apps_app.command("list")
def apps_list(
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """List all deployed applications."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        apps = list_apps_with_status(host, ctx=ctx)

    if not apps:
        console.print("No apps deployed.")
        raise typer.Exit()

    table = Table()
    table.add_column("Name")
    table.add_column("Method")
    table.add_column("URL")
    table.add_column("Status")

    for a in apps:
        table.add_row(a.name, a.method, a.url, a.status or "unknown")

    console.print(table)

# %% [markdown]
# ### apps status

# %%
#|export
@apps_app.command("status")
def apps_status(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Show detailed status for an app."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        try:
            status = app_status(host, name, ctx=ctx)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1)

    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Name", status.name)
    table.add_row("Method", status.method)
    table.add_row("URL", status.url)
    table.add_row("Routing", status.routing)
    table.add_row("Port", str(status.port) if status.port else "-")
    table.add_row("Status", status.status)
    if status.source:
        table.add_row("Source", status.source)
    if status.source_type:
        table.add_row("Source Type", status.source_type)
    if status.created_at:
        table.add_row("Created", status.created_at)
    if status.updated_at:
        table.add_row("Updated", status.updated_at)

    console.print(table)

# %% [markdown]
# ### apps stop / start / restart

# %%
#|export
@apps_app.command("stop")
def apps_stop(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Stop an app."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        stop_app(host, name, ctx=ctx)
    console.print(f"App [bold]{name}[/bold] stopped.")

# %%
#|export
@apps_app.command("start")
def apps_start(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Start an app."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        start_app(host, name, ctx=ctx)
    console.print(f"App [bold]{name}[/bold] started.")

# %%
#|export
@apps_app.command("restart")
def apps_restart(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Restart an app."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        restart_app(host, name, ctx=ctx)
    console.print(f"App [bold]{name}[/bold] restarted.")

# %% [markdown]
# ### apps logs

# %%
#|export
@apps_app.command("logs")
def apps_logs(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of log lines"),
):
    """Show logs for an app."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        output = app_logs(host, name, lines=lines, ctx=ctx)
    console.print(output)

# %% [markdown]
# ### apps remove

# %%
#|export
@apps_app.command("remove")
def apps_remove(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
    keep_data: bool = typer.Option(False, "--keep-data", help="Preserve the data/ directory"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove an app and all its resources."""
    validate_app_name(name)
    if not yes:
        confirm = typer.confirm(f"Remove app '{name}'? This cannot be undone.")
        if not confirm:
            raise typer.Abort()

    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    ctx = make_remote_context(srv)
    with ssh_connect(srv) as host:
        try:
            remove_app(host, name, keep_data=keep_data, ctx=ctx)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1)

    console.print(f"App [bold]{name}[/bold] removed.")

# %% [markdown]
# ### apps redeploy

# %%
#|export
@apps_app.command("redeploy")
def apps_redeploy(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Redeploy an app (update source, rebuild, restart)."""
    validate_app_name(name)
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"Redeploying [bold]{name}[/bold]...")
    with ssh_connect(srv) as host:
        try:
            redeploy_app(srv, host, name)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1)

    console.print(f"App [bold]{name}[/bold] redeployed.")

# %% [markdown]
# ## Tunnel subcommand group

# %%
#|export
tunnel_app = typer.Typer(
    name="tunnel",
    help="Manage localhost tunnels.",
    no_args_is_help=True,
)
app.add_typer(tunnel_app, name="tunnel")

# %% [markdown]
# ### tunnel open

# %%
#|export
@tunnel_app.command("open")
def tunnel_open(
    local_port: int = typer.Argument(help="Local port to expose"),
    url: str = typer.Option(..., "--url", help="Public URL for the tunnel"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Open a tunnel to expose a local port with HTTPS."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    open_tunnel(srv, local_port, url)

# %% [markdown]
# ### tunnel list

# %%
#|export
@tunnel_app.command("list")
def tunnel_list(
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """List active tunnels."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        tunnels = list_tunnels(host)

    if not tunnels:
        console.print("No active tunnels.")
        raise typer.Exit()

    table = Table()
    table.add_column("Tunnel ID")
    table.add_column("URL")
    table.add_column("Local Port")
    table.add_column("Remote Port")
    table.add_column("Created")

    for t in tunnels:
        table.add_row(t.tunnel_id, t.url, str(t.local_port), str(t.remote_port), t.created_at)

    console.print(table)

# %% [markdown]
# ### tunnel close

# %%
#|export
@tunnel_app.command("close")
def tunnel_close(
    tunnel_id: str = typer.Argument(help="Tunnel ID to close"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Close a tunnel and clean up resources."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    close_tunnel(srv, tunnel_id)
    console.print(f"Tunnel [bold]{tunnel_id}[/bold] closed.")

# %% [markdown]
# ### tunnel cleanup

# %%
#|export
@tunnel_app.command("cleanup")
def tunnel_cleanup(
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Remove stale tunnels whose SSH connections are dead."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    cleaned = cleanup_stale_tunnels(srv)
    if cleaned:
        for tid in cleaned:
            console.print(f"Cleaned up stale tunnel: {tid}")
    else:
        console.print("No stale tunnels found.")

# %% [markdown]
# ## Config subcommand group

# %%
#|export
config_app = typer.Typer(
    name="config",
    help="View configuration.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")

# %% [markdown]
# ### config show

# %%
#|export
@config_app.command("show")
def config_show():
    """Print the current configuration file."""
    p = config_path()
    if not p.exists():
        console.print("No configuration file found.")
        raise typer.Exit()
    console.print(p.read_text())

# %% [markdown]
# ## Entry point

# %%
#|export
def app_main() -> None:
    """Entry point for the appgarden CLI."""
    try:
        app()
    except ValueError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise SystemExit(1)
