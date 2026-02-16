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
import re
import shlex
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
# ## Input validation
#
# Centralised validators for user-provided strings that end up in
# shell commands, file paths, or config templates.

# %%
#|export
_APP_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*\Z')
_DOMAIN_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\Z')
_PATH_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*\Z')
_BRANCH_RE = re.compile(r'^[a-zA-Z0-9._/-]+\Z')
_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\Z')

def validate_app_name(name: str) -> str:
    """Validate an app name for use in paths and shell commands."""
    if not _APP_NAME_RE.match(name) or '..' in name:
        raise ValueError(f"Invalid app name '{name}': must start with alphanumeric, contain only [a-zA-Z0-9._-], no '..'")
    return name

def validate_domain(domain: str) -> str:
    """Validate a domain name."""
    if not _DOMAIN_RE.match(domain) or len(domain) > 253:
        raise ValueError(f"Invalid domain '{domain}'")
    return domain

def validate_url_path(path: str) -> str:
    """Validate a URL path segment (no slashes, dots, or special chars)."""
    if not _PATH_RE.match(path):
        raise ValueError(f"Invalid URL path '{path}': must match [a-zA-Z0-9_-]")
    return path

def validate_branch(branch: str) -> str:
    """Validate a git branch name."""
    if not _BRANCH_RE.match(branch) or '..' in branch:
        raise ValueError(f"Invalid branch '{branch}'")
    return branch

def validate_env_key(key: str) -> str:
    """Validate an environment variable key."""
    if not _ENV_KEY_RE.match(key):
        raise ValueError(f"Invalid env var key '{key}'")
    return key

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
def _make_ssh_state(server: ServerConfig, connect_timeout: int = 30,
                    ssh_key_password: str | None = None):
    """Build pyinfra Inventory/State for an SSH connection."""
    host_addr = resolve_host(server)
    ssh_key = str(Path(server.ssh_key).expanduser())

    override_data = {
        "ssh_user": server.ssh_user,
        "ssh_key": ssh_key,
        "ssh_strict_host_key_checking": "accept-new",
    }
    if ssh_key_password is not None:
        override_data["ssh_key_password"] = ssh_key_password

    inventory = Inventory(([host_addr], {}), override_data=override_data)
    config = Config(CONNECT_TIMEOUT=connect_timeout)
    state = State(inventory, config)
    state.init(inventory, config)
    return inventory, state

@contextmanager
def ssh_connect(server: ServerConfig, connect_timeout: int = 30, retries: int = 3):
    """Context manager that yields a connected pyinfra Host object.

    If the SSH key is encrypted, prompts for the passphrase.

    Usage::

        with ssh_connect(server_config) as host:
            ok, out = host.run_shell_command("hostname")
    """
    import time
    from getpass import getpass

    inventory, state = _make_ssh_state(server, connect_timeout)
    host = list(inventory)[0]

    # Retry connection for freshly provisioned servers
    last_err = None
    for attempt in range(retries):
        try:
            host.connect(raise_exceptions=True)
            last_err = None
            break
        except Exception as e:
            # Detect encrypted key error — prompt for passphrase and rebuild
            if "encrypted" in str(e).lower() and attempt == 0:
                ssh_key = str(Path(server.ssh_key).expanduser())
                password = getpass(f"SSH key passphrase ({ssh_key}): ")
                inventory, state = _make_ssh_state(server, connect_timeout, ssh_key_password=password)
                host = list(inventory)[0]
                try:
                    host.connect(raise_exceptions=True)
                    last_err = None
                    break
                except Exception as e2:
                    last_err = e2
                    break
            last_err = e
            if attempt < retries - 1:
                time.sleep(5)
    if last_err is not None:
        host_addr = resolve_host(server)
        raise ConnectionError(
            f"Failed to connect to {server.ssh_user}@{host_addr}: {last_err}"
        ) from last_err

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
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted garden.json on server: {e}. You may need to re-run 'server init'.")

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
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted ports.json on server: {e}. You may need to re-run 'server init'.")

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
    """Upload a local directory to the remote server using rsync.

    Uses the SSH agent when available (needed for encrypted keys).
    Falls back to specifying the key file directly.
    """
    import subprocess
    import os

    host_addr = resolve_host(server)
    ssh_key = str(Path(server.ssh_key).expanduser())
    local = str(Path(local_path).resolve())
    if not local.endswith("/"):
        local += "/"

    # If an SSH agent is running, let it handle auth (supports encrypted keys).
    # Still pass -i so the agent knows which key to offer.
    ssh_opts = f"ssh -o StrictHostKeyChecking=accept-new -i {shlex.quote(ssh_key)}"

    cmd = [
        "rsync", "-az", "--delete",
        "-e", ssh_opts,
        local,
        f"{server.ssh_user}@{host_addr}:{remote_path}/",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("'rsync' is not installed. Install it to deploy local source directories.")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        if e.returncode == 255:
            raise RuntimeError(
                f"SSH connection failed during rsync. "
                f"If your key is encrypted, ensure ssh-agent is running and your key is loaded:\n"
                f"  eval $(ssh-agent) && ssh-add {shlex.quote(ssh_key)}\n"
                f"rsync stderr: {stderr}"
            )
        if e.returncode == 23 and "permission denied" in stderr.lower():
            raise RuntimeError(
                f"Permission denied writing to {remote_path} on the server. "
                f"The directory may be owned by root. Fix with:\n"
                f"  appgarden server init --include group\n"
                f"or manually: ssh {server.ssh_user}@{host_addr} sudo chown -R {server.ssh_user} {remote_path}"
            )
        raise RuntimeError(f"rsync failed (exit {e.returncode}): {stderr}")

# %% [markdown]
# ## File locking
#
# Use ``flock`` on the remote server to prevent concurrent state
# file corruption when multiple clients run simultaneously.

# %%
#|export
def _lock_path(ctx: RemoteContext | None = None) -> str:
    """Return the path to the remote lock file."""
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/.appgarden.lock"

def read_garden_state_locked(host, ctx: RemoteContext | None = None) -> dict:
    """Read garden state under flock."""
    lock = _lock_path(ctx)
    path = garden_state_path(ctx)
    raw = run_remote_command(host, f"flock -w 10 {shlex.quote(lock)} cat {shlex.quote(path)}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted garden.json on server: {e}. You may need to re-run 'server init'.")

def write_garden_state_locked(host, state: dict, ctx: RemoteContext | None = None) -> None:
    """Write garden state under flock (write tmp, then atomic mv under lock)."""
    path = garden_state_path(ctx)
    lock = _lock_path(ctx)
    content = json.dumps(state, indent=2)
    tmp = f"{path}.tmp"
    write_remote_file(host, tmp, content)
    run_remote_command(host, f"flock -w 10 {shlex.quote(lock)} mv {shlex.quote(tmp)} {shlex.quote(path)}")

def read_ports_state_locked(host, ctx: RemoteContext | None = None) -> dict:
    """Read ports state under flock."""
    lock = _lock_path(ctx)
    path = ports_path(ctx)
    raw = run_remote_command(host, f"flock -w 10 {shlex.quote(lock)} cat {shlex.quote(path)}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted ports.json on server: {e}. You may need to re-run 'server init'.")

def write_ports_state_locked(host, state: dict, ctx: RemoteContext | None = None) -> None:
    """Write ports state under flock (write tmp, then atomic mv under lock)."""
    path = ports_path(ctx)
    lock = _lock_path(ctx)
    content = json.dumps(state, indent=2)
    tmp = f"{path}.tmp"
    write_remote_file(host, tmp, content)
    run_remote_command(host, f"flock -w 10 {shlex.quote(lock)} mv {shlex.quote(tmp)} {shlex.quote(path)}")
