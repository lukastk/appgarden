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
from io import StringIO
from pathlib import Path

from pyinfra.api import Config, Inventory, State

from appgarden.config import ServerConfig, resolve_host

# %% [markdown]
# ## Constants

# %%
#|export
APPGARDEN_ROOT = "/srv/appgarden"
GARDEN_STATE_PATH = f"{APPGARDEN_ROOT}/garden.json"
PORTS_PATH = f"{APPGARDEN_ROOT}/ports.json"

# %% [markdown]
# ## SSH connection
#
# We use pyinfra's low-level host API (`host.connect`, `host.run_shell_command`,
# `host.put_file`, `host.get_file`) for direct control over each operation.

# %%
#|export
@contextmanager
def ssh_connect(server: ServerConfig):
    """Context manager that yields a connected pyinfra Host object.

    Usage::

        with ssh_connect(server_config) as host:
            ok, out = host.run_shell_command("hostname")
    """
    host_addr = resolve_host(server)
    ssh_key = str(Path(server.ssh_key).expanduser())

    inventory = Inventory(
        ([host_addr], {}),
        ssh_user=server.ssh_user,
        ssh_key=ssh_key,
    )
    config = Config(CONNECT_TIMEOUT=10)
    state = State(inventory, config)
    state.init(inventory, config)

    host = list(inventory)[0]
    host.connect()
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
    buf = StringIO()
    ok = host.get_file(remote_filename=path, filename_or_io=buf,
                       print_output=False, print_input=False)
    if not ok:
        raise RuntimeError(f"Failed to read remote file: {path}")
    return buf.getvalue()

# %%
#|export
def write_remote_file(host, path: str, content: str) -> None:
    """Write text content to a file on the remote server."""
    buf = StringIO(content)
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
    )
    if not ok:
        stderr = "\n".join(output.stderr()) if output else ""
        raise RuntimeError(f"Remote command failed: {cmd}\n{stderr}")
    return "\n".join(output.stdout())

# %% [markdown]
# ## Garden state (garden.json)

# %%
#|export
def read_garden_state(host) -> dict:
    """Read the garden state from ``/srv/appgarden/garden.json``."""
    raw = read_remote_file(host, GARDEN_STATE_PATH)
    return json.loads(raw)

# %%
#|export
def write_garden_state(host, state: dict) -> None:
    """Write the garden state to ``/srv/appgarden/garden.json``."""
    content = json.dumps(state, indent=2)
    write_remote_file(host, GARDEN_STATE_PATH, content)

# %% [markdown]
# ## Ports state (ports.json)

# %%
#|export
def read_ports_state(host) -> dict:
    """Read port allocations from ``/srv/appgarden/ports.json``."""
    raw = read_remote_file(host, PORTS_PATH)
    return json.loads(raw)

# %%
#|export
def write_ports_state(host, state: dict) -> None:
    """Write port allocations to ``/srv/appgarden/ports.json``."""
    content = json.dumps(state, indent=2)
    write_remote_file(host, PORTS_PATH, content)

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
