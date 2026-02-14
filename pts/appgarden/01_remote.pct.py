# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp remote

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Remote Operations
#
# Read/write state on remote servers via pyinfra over SSH.

# %%
#|export
import json
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path

from pyinfra.api import Config, Inventory, State

from appgarden.config import ServerConfig, resolve_host

# %% [markdown]
# ## Constants

# %%
#|export
DEFAULT_APP_ROOT = "/srv/appgarden"
APPGARDEN_ROOT = DEFAULT_APP_ROOT
GARDEN_STATE_PATH = f"{APPGARDEN_ROOT}/garden.json"
PORTS_PATH = f"{APPGARDEN_ROOT}/ports.json"

# %% [markdown]
# ## RemoteContext
#
# Bundles per-server remote settings: the app root directory
# and whether sudo is needed for privileged operations.

# %%
#|export
@dataclass
class RemoteContext:
    """Per-server context for remote operations."""
    app_root: str = DEFAULT_APP_ROOT
    needs_sudo: bool = False  # auto-detected from ssh_user != "root"

# %%
#|export
def make_remote_context(server: ServerConfig) -> RemoteContext:
    """Create a RemoteContext from a ServerConfig."""
    return RemoteContext(
        app_root=server.app_root or DEFAULT_APP_ROOT,
        needs_sudo=(server.ssh_user != "root"),
    )

# %% [markdown]
# ## Path-building functions
#
# These return paths relative to the configured app root.

# %%
#|export
def garden_state_path(ctx: RemoteContext | None = None) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/garden.json"

def ports_path(ctx: RemoteContext | None = None) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/ports.json"

def caddy_apps_dir(ctx: RemoteContext | None = None) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/caddy/apps"

def caddy_tunnels_dir(ctx: RemoteContext | None = None) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/caddy/tunnels"

def app_dir(ctx: RemoteContext | None, name: str) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/apps/{name}"

def source_dir(ctx: RemoteContext | None, name: str) -> str:
    return f"{app_dir(ctx, name)}/source"

def tunnels_state_path(ctx: RemoteContext | None = None) -> str:
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/tunnels/active.json"

# %% [markdown]
# ## Sudo helpers
#
# pyinfra natively supports ``_sudo=True`` on ``run_shell_command`` and
# ``put_file``/``get_file``.  The helpers below pass this flag through
# when ``ctx.needs_sudo`` is set.

# %%
#|export
def _sudo_kwargs(ctx: RemoteContext | None) -> dict:
    """Return pyinfra kwargs for sudo if needed."""
    if ctx and ctx.needs_sudo:
        return {"_sudo": True}
    return {}

# %%
#|export
def run_sudo_command(host, cmd: str, ctx: RemoteContext | None = None, timeout: int = 30) -> str:
    """Run a shell command with sudo via pyinfra's native _sudo support."""
    sudo_kw = _sudo_kwargs(ctx)
    ok, output = host.run_shell_command(
        command=cmd, print_output=False, print_input=False,
        _timeout=timeout, **sudo_kw,
    )
    if not ok:
        stderr = output.stderr if output else ""
        raise RuntimeError(f"Remote command failed: {cmd}\n{stderr}")
    return output.stdout

# %%
#|export
def write_system_file(host, path: str, content: str, ctx: RemoteContext | None = None) -> None:
    """Write a file to a privileged location using pyinfra's native sudo.

    pyinfra's ``put_file`` with ``_sudo=True`` automatically handles
    uploading to a temp file, then copying into place with sudo.
    """
    sudo_kw = _sudo_kwargs(ctx)
    buf = BytesIO(content.encode("utf-8"))
    ok = host.put_file(filename_or_io=buf, remote_filename=path,
                       print_output=False, print_input=False, **sudo_kw)
    if not ok:
        raise RuntimeError(f"Failed to write system file: {path}")

# %% [markdown]
# ## SSH connection
#
# We use pyinfra's low-level host API (`host.connect`, `host.run_shell_command`,
# `host.put_file`, `host.get_file`) for direct control over each operation.

# %%
#|export
@contextmanager
def ssh_connect(server: ServerConfig, connect_timeout: int = 30, retries: int = 3):
    """Context manager that yields a connected pyinfra Host object.

    Usage::

        with ssh_connect(server_config) as host:
            ok, out = host.run_shell_command("hostname")
    """
    import time

    host_addr = resolve_host(server)
    ssh_key = str(Path(server.ssh_key).expanduser())

    inventory = Inventory(
        ([host_addr], {}),
        override_data={
            "ssh_user": server.ssh_user,
            "ssh_key": ssh_key,
            "ssh_strict_host_key_checking": "no",
        },
    )
    config = Config(CONNECT_TIMEOUT=connect_timeout)
    state = State(inventory, config)
    state.init(inventory, config)

    host = list(inventory)[0]

    # Retry connection for freshly provisioned servers
    last_err = None
    for attempt in range(retries):
        try:
            host.connect(raise_exceptions=True)
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(5)
    if last_err is not None:
        raise last_err

    try:
        yield host
    finally:
        host.disconnect()

# %% [markdown]
# ## Remote file helpers

# %%
#|export
def read_remote_file(host, path: str) -> str:
    """Read a text file from the remote server."""
    buf = BytesIO()
    ok = host.get_file(remote_filename=path, filename_or_io=buf,
                       print_output=False, print_input=False)
    if not ok:
        raise RuntimeError(f"Failed to read remote file: {path}")
    return buf.getvalue().decode("utf-8")

# %%
#|export
def write_remote_file(host, path: str, content: str) -> None:
    """Write text content to a file on the remote server."""
    buf = BytesIO(content.encode("utf-8"))
    ok = host.put_file(filename_or_io=buf, remote_filename=path,
                       print_output=False, print_input=False)
    if not ok:
        raise RuntimeError(f"Failed to write remote file: {path}")

# %%
#|export
def run_remote_command(host, cmd: str, timeout: int = 30) -> str:
    """Run a shell command on the remote and return stdout."""
    ok, output = host.run_shell_command(
        command=cmd, print_output=False, print_input=False,
        _timeout=timeout,
    )
    if not ok:
        stderr = output.stderr if output else ""
        raise RuntimeError(f"Remote command failed: {cmd}\n{stderr}")
    return output.stdout

# %% [markdown]
# ## Garden state (garden.json)

# %%
#|export
def read_garden_state(host, ctx: RemoteContext | None = None) -> dict:
    """Read the garden state from garden.json."""
    raw = read_remote_file(host, garden_state_path(ctx))
    return json.loads(raw)

# %%
#|export
def write_garden_state(host, state: dict, ctx: RemoteContext | None = None) -> None:
    """Write the garden state to garden.json."""
    content = json.dumps(state, indent=2)
    write_remote_file(host, garden_state_path(ctx), content)

# %% [markdown]
# ## Ports state (ports.json)

# %%
#|export
def read_ports_state(host, ctx: RemoteContext | None = None) -> dict:
    """Read port allocations from ports.json."""
    raw = read_remote_file(host, ports_path(ctx))
    return json.loads(raw)

# %%
#|export
def write_ports_state(host, state: dict, ctx: RemoteContext | None = None) -> None:
    """Write port allocations to ports.json."""
    content = json.dumps(state, indent=2)
    write_remote_file(host, ports_path(ctx), content)

# %% [markdown]
# ## Directory upload
#
# Uses rsync via a shell command for efficiency.

# %%
#|export
def upload_directory(server: ServerConfig, local_path: str | Path, remote_path: str) -> None:
    """Upload a local directory to the remote server using rsync."""
    import subprocess

    host_addr = resolve_host(server)
    ssh_key = str(Path(server.ssh_key).expanduser())
    local = str(Path(local_path).resolve())
    if not local.endswith("/"):
        local += "/"

    cmd = [
        "rsync", "-az", "--delete",
        "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new",
        local,
        f"{server.ssh_user}@{host_addr}:{remote_path}/",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
