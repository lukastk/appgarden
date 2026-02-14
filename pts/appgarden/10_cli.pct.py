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
from appgarden.server import init_server, ping_server
from appgarden.deploy import deploy_static, deploy_command, deploy_docker_compose, deploy_dockerfile
from appgarden.auto_docker import deploy_auto
from appgarden.apps import (
    list_apps, list_apps_with_status, app_status,
    stop_app, start_app, restart_app,
    remove_app, redeploy_app, app_logs,
)
from appgarden.remote import ssh_connect
from appgarden.environments import load_project_config, resolve_environment, resolve_all_environments

# %% [markdown]
# # CLI Application
#
# Typer-based CLI for deploying web applications to remote servers.

# %%
#|export
app = typer.Typer(
    name="appgarden",
    help="Deploy web applications to remote servers.",
    no_args_is_help=True,
)
console = Console()

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
):
    """Add a server to the configuration."""
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
):
    """Initialise a server for AppGarden (installs Docker, Caddy, etc.)."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    init_server(srv)

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
        result[k] = v
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
def _deploy_env(cfg, env_cfg, server_override: str | None = None) -> None:
    """Deploy a single resolved environment config."""
    server_name = server_override or env_cfg.server
    try:
        sname, srv = get_server(cfg, server_name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    if not env_cfg.url:
        console.print(f"[red]Error:[/red] No URL defined for environment '{env_cfg.name}'")
        raise typer.Exit(code=1)

    if not env_cfg.method:
        console.print(f"[red]Error:[/red] No method defined for environment '{env_cfg.name}'")
        raise typer.Exit(code=1)

    console.print(f"Deploying [bold]{env_cfg.app_name}[/bold] ({env_cfg.name}) to {env_cfg.url}...")
    _dispatch_deploy(
        srv, env_cfg.app_name, env_cfg.method, env_cfg.url,
        source=env_cfg.source, port=env_cfg.port,
        container_port=env_cfg.container_port or 3000,
        cmd=env_cfg.cmd, setup_cmd=env_cfg.setup_cmd,
        branch=env_cfg.branch,
        env_vars=env_cfg.env or None,
        env_file=env_cfg.env_file,
    )

# %%
#|export
@app.command()
def deploy(
    name: str = typer.Argument(help="App name or environment name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
    method: Optional[str] = typer.Option(None, "--method", "-m", help="Deployment method (static, command, docker-compose, dockerfile, auto)"),
    source: Optional[str] = typer.Option(None, "--source", help="Source path or git URL"),
    url: Optional[str] = typer.Option(None, "--url", help="URL for the app"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Host port (auto-allocated if omitted)"),
    container_port: int = typer.Option(3000, "--container-port", help="Container port (for dockerfile/auto methods)"),
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

    # Check for appgarden.toml-based deployment
    if all_envs:
        try:
            project = load_project_config()
        except FileNotFoundError:
            console.print("[red]Error:[/red] No appgarden.toml found in current directory")
            raise typer.Exit(code=1)
        envs = resolve_all_environments(project)
        for env_cfg in envs:
            _deploy_env(cfg, env_cfg, server_override=server)
        return

    # Try environment-based deploy if appgarden.toml exists
    try:
        project = load_project_config()
        if name in project.environments:
            env_cfg = resolve_environment(project, name)
            _deploy_env(cfg, env_cfg, server_override=server)
            return
    except FileNotFoundError:
        pass

    # Fall back to explicit flag-based deployment
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    if not url:
        console.print("[red]Error:[/red] --url is required")
        raise typer.Exit(code=1)

    if not method:
        method = "static"

    env_vars = _parse_env_list(env)
    _dispatch_deploy(
        srv, name, method, url,
        source=source, port=port, container_port=container_port,
        cmd=cmd, setup_cmd=setup_cmd, branch=branch,
        env_vars=env_vars, env_file=env_file,
    )

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

    with ssh_connect(srv) as host:
        apps = list_apps_with_status(host)

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
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        try:
            status = app_status(host, name)
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
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        stop_app(host, name)
    console.print(f"App [bold]{name}[/bold] stopped.")

# %%
#|export
@apps_app.command("start")
def apps_start(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Start an app."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        start_app(host, name)
    console.print(f"App [bold]{name}[/bold] started.")

# %%
#|export
@apps_app.command("restart")
def apps_restart(
    name: str = typer.Argument(help="App name"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name"),
):
    """Restart an app."""
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        restart_app(host, name)
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
    cfg = load_config()
    try:
        sname, srv = get_server(cfg, server)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    with ssh_connect(srv) as host:
        output = app_logs(host, name, lines=lines)
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

    with ssh_connect(srv) as host:
        try:
            remove_app(host, name, keep_data=keep_data)
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
    app()
