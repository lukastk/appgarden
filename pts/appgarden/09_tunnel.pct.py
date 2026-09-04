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
import os
import random
import shlex
import signal
import subprocess
import sys
import time
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
    tunnels_lock_path, update_json_state_locked,
)
from appgarden.ports import allocate_port, release_port
from appgarden.routing import generate_caddy_config, replace_snippet_and_reload, CADDY_TUNNELS_DIR

console = Console()

# %% [markdown]
# ## Constants

# %%
#|export
TUNNELS_STATE_FILE = f"{APPGARDEN_ROOT}/tunnels/active.json"

# %% [markdown]
# ## Random subdomain generation

# %%
#|export
_TUNNEL_WORDS = [
    "apple", "banana", "cherry", "lemon", "mango", "orange", "peach", "plum", "grape", "melon",
    "table", "chair", "desk", "lamp", "mirror", "window", "door", "shelf", "drawer", "ladder",
    "tower", "bridge", "castle", "harbor", "lighthouse", "palace", "garden", "cottage", "mansion", "skyscraper",
    "river", "ocean", "mountain", "valley", "forest", "meadow", "desert", "island", "glacier", "canyon",
    "sun", "moon", "star", "planet", "comet", "cloud", "rainbow", "thunder", "breeze", "mist",
    "fox", "wolf", "bear", "deer", "otter", "rabbit", "panda", "tiger", "lynx", "badger",
    "robin", "sparrow", "eagle", "falcon", "owl", "swan", "crane", "heron", "raven", "finch",
    "piano", "violin", "drum", "flute", "guitar", "harp", "trumpet", "cello", "banjo", "ukulele",
    "book", "pencil", "candle", "button", "ribbon", "basket", "bottle", "kettle", "teapot", "compass",
    "amber", "crimson", "indigo", "jade", "silver", "copper", "ivory", "marble", "opal", "ruby",
]

# %%
#|export
def _random_subdomain(domain: str) -> str:
    """Generate a random three-word subdomain like 'apple-skyscraper-table.example.com'."""
    parts = [random.choice(_TUNNEL_WORDS) for _ in range(3)]
    return f"{'-'.join(parts)}.{domain}"

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
    """Read the active tunnels state from the server. Returns an empty state if the file does not exist yet."""
    path = tunnels_state_path(ctx) if ctx else TUNNELS_STATE_FILE
    try:
        content = read_remote_file(host, path)
    except FileNotFoundError:
        return {"tunnels": {}}
    return json.loads(content)

# %%
#|export
def _update_tunnels_state(host, mutate, ctx: RemoteContext | None = None) -> dict:
    """Apply *mutate* to active.json as a locked compare-and-swap update.

    Replacing the file via mv needs only directory write permission, so the
    file's ownership never matters (a direct SFTP overwrite would let a
    root-entry write lock out the box's non-root deploy users — same disease
    as the host ports registry in issue #18), and the CAS retry means two
    tunnels opened concurrently can't lose each other's registration.
    The file is created on first use (``default``).
    """
    path = tunnels_state_path(ctx) if ctx else TUNNELS_STATE_FILE
    return update_json_state_locked(
        host, path, tunnels_lock_path(ctx), mutate,
        default={"tunnels": {}},
        corrupt_hint=f"Corrupted tunnels state on server: {path}",
    )

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
    """Deploy a temporary Caddy config for the tunnel.

    Uses ``replace_snippet_and_reload``: if the post-write `caddy reload`
    fails (e.g. because another snippet on the server already claims the same
    site), the freshly-written snippet is rolled back before re-raising.
    Without that, every failed `tunnel open` would leave an orphan
    `<tunnel_id>.caddy` file behind, and subsequent attempts against the same
    URL would hit ``ambiguous site definition`` from Caddy and fail forever.
    """
    config = generate_caddy_config(
        domain=domain,
        port=remote_port,
    )
    caddy_path = _tunnel_caddy_path(tunnel_id, ctx)
    replace_snippet_and_reload(host, caddy_path, config, ctx=ctx)

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
    entry = {
        "url": url,
        "local_port": local_port,
        "remote_port": remote_port,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    def _mut(state: dict) -> dict:
        state.setdefault("tunnels", {})[tunnel_id] = entry
        return state
    _update_tunnels_state(host, _mut, ctx=ctx)

# %%
#|export
def _unregister_tunnel(host, tunnel_id: str, ctx: RemoteContext | None = None) -> None:
    """Remove the tunnel from active.json."""
    def _mut(state: dict) -> dict:
        state.setdefault("tunnels", {}).pop(tunnel_id, None)
        return state
    _update_tunnels_state(host, _mut, ctx=ctx)

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

# %%
#|export
def _close_tunnels_for_url(server: ServerConfig, url: str, ctx: RemoteContext | None = None) -> list[str]:
    """Close every registered tunnel already claiming *url*; returns the ids closed.

    A tunnel's Caddy snippet is keyed by its (freshly minted) tunnel id, so a run
    killed without cleanup — a reboot, a SIGKILL — leaves a snippet behind that
    still claims the hostname. The next `open` for the same url then fails Caddy's
    reload with "ambiguous site definition" and rolls itself back, so a supervised
    service could never reclaim its own URL after a hard stop.
    """
    if ctx is None:
        ctx = make_remote_context(server)
    with ssh_connect(server) as host:
        state = _read_tunnels_state(host, ctx=ctx)
    stale = [tid for tid, data in state.get("tunnels", {}).items() if data.get("url") == url]
    for tid in stale:
        _cleanup_tunnel(server, tid, tid, ctx=ctx)
    return stale

# %% [markdown]
# ## open_tunnel (main entry point)

# %%
#|export
def _spawn_serve(
    path: str,
    port: int,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
) -> subprocess.Popen:
    """Spawn a local HTTP server exposing ``path`` (file or directory) on ``port``.

    Directories are served via the ``appgarden._dir_server`` module, which supports
    include/exclude glob filters (matched against the path-relative-to-root or any
    individual path component). Files are served via a tiny inline server that
    returns the file contents (with guessed Content-Type) at any request path;
    include/exclude are not meaningful in file mode. Bound to 127.0.0.1 — exposure
    happens over the SSH tunnel.
    """
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path):
        raise RuntimeError(f"--serve path does not exist: {path}")

    includes = list(includes or [])
    excludes = list(excludes or [])

    if os.path.isdir(abs_path):
        console.print(f"[dim]Serving directory {abs_path} on localhost:{port}[/dim]")
        if includes:
            console.print(f"[dim]Include: {', '.join(includes)}[/dim]")
        if excludes:
            console.print(f"[dim]Exclude: {', '.join(excludes)}[/dim]")
        argv = [
            sys.executable, "-m", "appgarden._dir_server",
            str(port), abs_path, json.dumps(includes), json.dumps(excludes),
        ]
    else:
        if includes or excludes:
            raise RuntimeError("--include and --exclude only apply when --serve points to a directory")
        console.print(f"[dim]Serving file {abs_path} on localhost:{port}[/dim]")
        script = (
            "import http.server, mimetypes\n"
            f"target = {abs_path!r}\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def log_message(self, *a, **k): pass\n"
            "    def do_GET(self):\n"
            "        with open(target, 'rb') as f:\n"
            "            data = f.read()\n"
            "        ctype, _ = mimetypes.guess_type(target)\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', ctype or 'application/octet-stream')\n"
            "        self.send_header('Content-Length', str(len(data)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(data)\n"
            f"http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n"
        )
        argv = [sys.executable, "-c", script]

    return subprocess.Popen(argv, start_new_session=True)

# %%
#|export
def _kill_process_group(proc: subprocess.Popen) -> None:
    """Send SIGTERM to a subprocess's process group, escalating to SIGKILL if needed."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

# %%
#|export
def _sigterm_as_interrupt(signum, frame):
    """Turn SIGTERM into KeyboardInterrupt so shutdown takes the Ctrl+C path."""
    raise KeyboardInterrupt

# %%
#|export
def open_tunnel(
    server: ServerConfig,
    local_port: int,
    url: str | None = None,
    cmd: str | None = None,
    serve: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    close_on_cmd_exit: bool = False,
    replace: bool = False,
) -> None:
    """Open a tunnel: allocate port, configure Caddy, SSH reverse tunnel.

    If ``url`` is omitted, a random three-word subdomain is generated under
    ``server.domain`` (e.g. ``apple-skyscraper-table.example.com``).

    If ``cmd`` is given, it is run locally via the shell alongside the tunnel and
    killed (with its child process group) when the tunnel closes. If ``serve`` is
    given instead, a local HTTP server is spawned exposing the file or directory
    at that path on ``local_port``. ``cmd`` and ``serve`` are mutually exclusive.

    By default the tunnel stays up if the spawned process exits on its own; pass
    ``close_on_cmd_exit=True`` to tear down the tunnel as soon as it exits.

    ``replace=True`` first closes any tunnel already registered against ``url``,
    making the call idempotent — what a supervised service needs to reclaim its
    own URL after a hard stop left the previous registration behind. It requires
    an explicit ``url``.

    Blocks until Ctrl+C (or, with ``close_on_cmd_exit``, until the spawned
    process exits), then cleans up.
    """
    if cmd and serve:
        raise ValueError("`cmd` and `serve` are mutually exclusive")
    if replace and url is None:
        raise ValueError("`replace` requires an explicit `url` — a generated subdomain can never match an existing tunnel")

    if url is None:
        url = _random_subdomain(server.domain)

    ctx = make_remote_context(server)

    if replace:
        for tid in _close_tunnels_for_url(server, url, ctx=ctx):
            console.print(f"[dim]Replaced tunnel {tid} already claiming {url}[/dim]")
    tunnel_id = f"tunnel-{uuid.uuid4().hex[:8]}"
    app_name = tunnel_id
    host_ip = resolve_host(server)

    # 1. Allocate port and set up Caddy
    with ssh_connect(server) as host:
        remote_port = allocate_port(host, app_name)
        try:
            _deploy_tunnel_caddy(host, tunnel_id, url, remote_port, ctx=ctx)
            _register_tunnel(host, tunnel_id, url, local_port, remote_port, ctx=ctx)
        except BaseException:
            # Unwind: tunnel ids are fresh, so the allocation is always newly
            # created. Without this, a failed setup leaks the port — and since
            # the tunnel never reached active.json, `tunnel cleanup` can't
            # reap it (issue #24). The snippet may exist if registration was
            # the failing step; _remove_tunnel_caddy is a no-op otherwise.
            _remove_tunnel_caddy(host, tunnel_id, ctx=ctx)
            try:
                release_port(host, app_name)
            except (ValueError, RuntimeError) as e:
                console.print(f"[yellow]Warning:[/yellow] failed to release "
                              f"port {remote_port} for {tunnel_id}: {e}")
            raise

    console.print(f"[green]Tunnel open:[/green] https://{url} -> localhost:{local_port}")
    console.print(f"[dim]Remote port: {remote_port} | Tunnel ID: {tunnel_id}[/dim]")
    console.print("[dim]Press Ctrl+C to close the tunnel.[/dim]")

    # 2. Optionally start local command or file server in its own process group
    cmd_proc = None
    if cmd:
        console.print(f"[dim]Running: {cmd}[/dim]")
        cmd_proc = subprocess.Popen(cmd, shell=True, start_new_session=True)
    elif serve:
        cmd_proc = _spawn_serve(serve, local_port, includes=include, excludes=exclude)

    # 3. Open SSH reverse tunnel
    ssh_cmd = [
        "ssh", "-N",
        "-R", f"{remote_port}:localhost:{local_port}",
        "-i", server.ssh_key,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        # Without this, a remote bind that fails is only a warning: ssh stays up
        # holding a tunnel that forwards nothing, and Caddy proxies the URL into
        # a black hole (or onto whatever else holds the port) for as long as the
        # session lives. It happens for real — an ssh orphaned by a hard-killed
        # `tunnel open` keeps its old remote port bound after the allocation has
        # been released, so the next tunnel can be handed that very port. Exiting
        # instead turns that into a loud failure a caller can retry.
        "-o", "ExitOnForwardFailure=yes",
        f"{server.ssh_user}@{host_ip}",
    ]

    # SIGTERM is what a process supervisor (supervisord, systemd, docker stop)
    # sends, and Python's default action for it is to die on the spot — no
    # unwinding, so the `finally` below never runs and the tunnel's registration
    # and Caddy snippet are left claiming the hostname. Route it into the same
    # KeyboardInterrupt path Ctrl+C already takes so a supervised stop cleans up.
    prev_sigterm = signal.signal(signal.SIGTERM, _sigterm_as_interrupt)

    proc = None
    try:
        proc = subprocess.Popen(ssh_cmd)
        if cmd_proc is not None and close_on_cmd_exit:
            while True:
                if proc.poll() is not None:
                    break
                if cmd_proc.poll() is not None:
                    console.print(
                        f"\n[yellow]Command exited (code {cmd_proc.returncode}); closing tunnel...[/yellow]"
                    )
                    break
                time.sleep(0.5)
        else:
            proc.wait()
    except FileNotFoundError:
        raise RuntimeError("'ssh' command not found. Ensure OpenSSH is installed.")
    except KeyboardInterrupt:
        console.print("\n[yellow]Closing tunnel...[/yellow]")
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        if cmd_proc is not None and cmd_proc.poll() is None:
            _kill_process_group(cmd_proc)
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
