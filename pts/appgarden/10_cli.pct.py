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
