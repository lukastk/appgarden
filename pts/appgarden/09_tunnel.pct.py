# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp tunnel

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Localhost Tunneling
#
# Expose a locally running app through the remote server with HTTPS.
# Opens an SSH reverse tunnel and configures Caddy as a reverse proxy.

# %%
#|export
import json
import shlex
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console

from appgarden.config import ServerConfig, resolve_host
from appgarden.remote import (
    ssh_connect, run_remote_command, read_remote_file, write_remote_file,
    APPGARDEN_ROOT,
    RemoteContext, make_remote_context,
    privileged_systemctl, caddy_tunnels_dir, tunnels_state_path,
)
from appgarden.ports import allocate_port, release_port
from appgarden.routing import generate_caddy_config, CADDY_TUNNELS_DIR

console = Console()

# %% [markdown]
# ## Constants

# %%
#|export
TUNNELS_STATE_FILE = f"{APPGARDEN_ROOT}/tunnels/active.json"

# %% [markdown]
# ## Data model

# %%
#|export
@dataclass
class TunnelInfo:
    tunnel_id: str
    url: str
    local_port: int
    remote_port: int
    created_at: str

# %% [markdown]
# ## Tunnel state management

# %%
#|export
def _read_tunnels_state(host, ctx: RemoteContext | None = None) -> dict:
    """Read the active tunnels state from the server."""
    try:
        path = tunnels_state_path(ctx) if ctx else TUNNELS_STATE_FILE
        content = read_remote_file(host, path)
        return json.loads(content)
    except (RuntimeError, json.JSONDecodeError):
        return {"tunnels": {}}

# %%
#|export
def _write_tunnels_state(host, state: dict, ctx: RemoteContext | None = None) -> None:
    """Write the active tunnels state to the server."""
    path = tunnels_state_path(ctx) if ctx else TUNNELS_STATE_FILE
    write_remote_file(host, path, json.dumps(state, indent=2))

# %% [markdown]
# ## open_tunnel

# %%
#|export
def _tunnel_caddy_path(tunnel_id: str, ctx: RemoteContext | None = None) -> str:
    """Path for a tunnel's Caddy config file."""
    tunnels_dir = caddy_tunnels_dir(ctx) if ctx else CADDY_TUNNELS_DIR
    return f"{tunnels_dir}/{tunnel_id}.caddy"

# %%
#|export
def _deploy_tunnel_caddy(host, tunnel_id: str, domain: str, remote_port: int, ctx: RemoteContext | None = None) -> None:
    """Deploy a temporary Caddy config for the tunnel."""
    config = generate_caddy_config(
        domain=domain,
        port=remote_port,
    )
    caddy_path = _tunnel_caddy_path(tunnel_id, ctx)
    write_remote_file(host, caddy_path, config)
    privileged_systemctl(host, "reload", "caddy", ctx=ctx)

# %%
#|export
def _remove_tunnel_caddy(host, tunnel_id: str, ctx: RemoteContext | None = None) -> None:
    """Remove a tunnel's Caddy config and reload."""
    caddy_path = _tunnel_caddy_path(tunnel_id, ctx)
    try:
        run_remote_command(host, f"rm -f {shlex.quote(caddy_path)}")
        privileged_systemctl(host, "reload", "caddy", ctx=ctx)
    except RuntimeError:
        pass

# %%
#|export
def _register_tunnel(host, tunnel_id: str, url: str, local_port: int, remote_port: int, ctx: RemoteContext | None = None) -> None:
    """Record the tunnel in active.json."""
    state = _read_tunnels_state(host, ctx=ctx)
    state["tunnels"][tunnel_id] = {
        "url": url,
        "local_port": local_port,
        "remote_port": remote_port,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_tunnels_state(host, state, ctx=ctx)

# %%
#|export
def _unregister_tunnel(host, tunnel_id: str, ctx: RemoteContext | None = None) -> None:
    """Remove the tunnel from active.json."""
    state = _read_tunnels_state(host, ctx=ctx)
    state["tunnels"].pop(tunnel_id, None)
    _write_tunnels_state(host, state, ctx=ctx)

# %% [markdown]
# ## Cleanup and signal handling

# %%
#|export
def _cleanup_tunnel(server: ServerConfig, tunnel_id: str, app_name: str, ctx: RemoteContext | None = None) -> None:
    """Full cleanup: remove Caddy config, release port, unregister."""
    if ctx is None:
        ctx = make_remote_context(server)
    with ssh_connect(server) as host:
        _remove_tunnel_caddy(host, tunnel_id, ctx=ctx)
        try:
            release_port(host, app_name)
        except (ValueError, RuntimeError):
            pass
        _unregister_tunnel(host, tunnel_id, ctx=ctx)

# %% [markdown]
# ## open_tunnel (main entry point)

# %%
#|export
def open_tunnel(server: ServerConfig, local_port: int, url: str) -> None:
    """Open a tunnel: allocate port, configure Caddy, SSH reverse tunnel.

    Blocks until Ctrl+C, then cleans up.
    """
    ctx = make_remote_context(server)
    tunnel_id = f"tunnel-{uuid.uuid4().hex[:8]}"
    app_name = tunnel_id
    host_ip = resolve_host(server)

    # 1. Allocate port and set up Caddy
    with ssh_connect(server) as host:
        remote_port = allocate_port(host, app_name)
        _deploy_tunnel_caddy(host, tunnel_id, url, remote_port, ctx=ctx)
        _register_tunnel(host, tunnel_id, url, local_port, remote_port, ctx=ctx)

    console.print(f"[green]Tunnel open:[/green] https://{url} -> localhost:{local_port}")
    console.print(f"[dim]Remote port: {remote_port} | Tunnel ID: {tunnel_id}[/dim]")
    console.print("[dim]Press Ctrl+C to close the tunnel.[/dim]")

    # 2. Open SSH reverse tunnel
    ssh_cmd = [
        "ssh", "-N",
        "-R", f"{remote_port}:localhost:{local_port}",
        "-i", server.ssh_key,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        f"{server.ssh_user}@{host_ip}",
    ]

    proc = None
    try:
        proc = subprocess.Popen(ssh_cmd)
        proc.wait()
    except FileNotFoundError:
        raise RuntimeError("'ssh' command not found. Ensure OpenSSH is installed.")
    except KeyboardInterrupt:
        console.print("\n[yellow]Closing tunnel...[/yellow]")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        _cleanup_tunnel(server, tunnel_id, app_name, ctx=ctx)
        console.print("[green]Tunnel closed.[/green]")

# %% [markdown]
# ## close_tunnel (remote cleanup)

# %%
#|export
def close_tunnel(server: ServerConfig, tunnel_id: str) -> None:
    """Close a specific tunnel by ID (remote cleanup only)."""
    ctx = make_remote_context(server)
    app_name = tunnel_id
    _cleanup_tunnel(server, tunnel_id, app_name, ctx=ctx)

# %% [markdown]
# ## list_tunnels

# %%
#|export
def list_tunnels(host, ctx: RemoteContext | None = None) -> list[TunnelInfo]:
    """List all active tunnels from the server."""
    state = _read_tunnels_state(host, ctx=ctx)
    tunnels = []
    for tid, data in state.get("tunnels", {}).items():
        tunnels.append(TunnelInfo(
            tunnel_id=tid,
            url=data.get("url", ""),
            local_port=data.get("local_port", 0),
            remote_port=data.get("remote_port", 0),
            created_at=data.get("created_at", ""),
        ))
    return tunnels

# %% [markdown]
# ## cleanup_stale_tunnels

# %%
#|export
def cleanup_stale_tunnels(server: ServerConfig) -> list[str]:
    """Detect and remove tunnels whose SSH connections are dead.

    Returns list of cleaned-up tunnel IDs.
    """
    ctx = make_remote_context(server)
    cleaned = []
    with ssh_connect(server) as host:
        state = _read_tunnels_state(host, ctx=ctx)
        for tid, data in list(state.get("tunnels", {}).items()):
            remote_port = data.get("remote_port")
            if remote_port is None:
                continue
            remote_port = int(remote_port)
            # Check if anything is listening on the remote port
            try:
                result = run_remote_command(
                    host, f"ss -tln | grep -q ':{remote_port} ' && echo active || echo inactive"
                )
                if "inactive" in result:
                    close_tunnel(server, tid)
                    cleaned.append(tid)
            except RuntimeError:
                close_tunnel(server, tid)
                cleaned.append(tid)
    return cleaned
