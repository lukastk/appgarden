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
import copy
import json
import re
import shlex
import uuid
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

# Port allocations are a box-global resource: every garden on a host shares the
# same TCP port space, so the ports registry lives at one host-level path
# (independent of any garden's app_root) and all gardens allocate from it under a
# single lock. This is what stops two gardens on one host handing out the same
# host port. (Garden state — garden.json — stays per-``app_root``.)
HOST_STATE_DIR = "/var/lib/appgarden"
PORTS_PATH = f"{HOST_STATE_DIR}/ports.json"
HOST_PORTS_LOCK = f"{HOST_STATE_DIR}/.ports.lock"

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

def ports_path() -> str:
    """Path to the host-level shared ports registry (box-global, not per-garden)."""
    return PORTS_PATH

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

def tunnels_lock_path(ctx: RemoteContext | None = None) -> str:
    """Lock file guarding updates to the tunnels active.json."""
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/tunnels/.tunnels.lock"

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
# ## Privileged wrapper helpers
#
# For non-root users, privileged operations (systemctl, unit file management,
# journalctl) are routed through the `appgarden-privileged` wrapper script
# installed during `server init`.  Root users bypass the wrapper entirely.

# %%
#|export
PRIVILEGED_HELPER_PATH = "/usr/local/bin/appgarden-privileged"

# %%
#|export
def check_privileged_helper(host, ctx: RemoteContext | None = None) -> bool:
    """Check if the privileged wrapper script is installed on the server.

    Returns True if installed, False otherwise.
    """
    try:
        run_remote_command(host, f"test -x {PRIVILEGED_HELPER_PATH}")
        return True
    except RuntimeError:
        return False

# %%
#|export
def _require_privileged_helper(host, ctx: RemoteContext | None = None) -> None:
    """Raise if the privileged helper is needed but not installed."""
    if ctx and ctx.needs_sudo and not check_privileged_helper(host, ctx):
        raise RuntimeError(
            f"Privileged wrapper not found at {PRIVILEGED_HELPER_PATH}. "
            "Re-run 'appgarden server init' to install it."
        )

# %%
#|export
def privileged_systemctl(host, action: str, unit: str | None = None,
                         ctx: RemoteContext | None = None) -> str:
    """Run a systemctl action, routing through the privileged wrapper for non-root users.

    For root users (or ctx=None), executes directly via run_sudo_command.
    For non-root users, uses sudo appgarden-privileged systemctl <action> [unit].
    """
    if not ctx or not ctx.needs_sudo:
        # Root: execute directly
        if unit:
            return run_sudo_command(host, f"systemctl {action} {shlex.quote(unit)}", ctx=ctx)
        return run_sudo_command(host, f"systemctl {action}", ctx=ctx)

    # Non-root: route through wrapper
    _require_privileged_helper(host, ctx)
    cmd = f"sudo {PRIVILEGED_HELPER_PATH} systemctl {action}"
    if unit:
        cmd += f" {shlex.quote(unit)}"
    return run_remote_command(host, cmd)

# %%
#|export
def privileged_install_unit(host, name: str, content: str,
                            ctx: RemoteContext | None = None) -> None:
    """Install a systemd unit file, routing through the wrapper for non-root users.

    For root users, writes directly via write_system_file.
    For non-root users, writes a temp file and calls the wrapper to copy it.
    """
    if not ctx or not ctx.needs_sudo:
        unit_path = f"/etc/systemd/system/{name}"
        write_system_file(host, unit_path, content, ctx=ctx)
        return

    # Non-root: write temp file, then call wrapper. The random suffix keeps the
    # staging path unpredictable so another local user can't pre-create it (the
    # wrapper additionally verifies the file is a regular file owned by the
    # invoking user before installing it as a root unit).
    _require_privileged_helper(host, ctx)
    temp_path = f"/tmp/appgarden-unit-{uuid.uuid4().hex}.tmp"
    write_remote_file(host, temp_path, content)
    run_remote_command(host, f"sudo {PRIVILEGED_HELPER_PATH} install-unit {shlex.quote(name)} {shlex.quote(temp_path)}")

# %%
#|export
def privileged_remove_unit(host, name: str,
                           ctx: RemoteContext | None = None) -> None:
    """Remove a systemd unit file, routing through the wrapper for non-root users."""
    if not ctx or not ctx.needs_sudo:
        run_sudo_command(host, f"rm -f /etc/systemd/system/{shlex.quote(name)}", ctx=ctx)
        return

    _require_privileged_helper(host, ctx)
    run_remote_command(host, f"sudo {PRIVILEGED_HELPER_PATH} remove-unit {shlex.quote(name)}")

# %%
#|export
def privileged_journalctl(host, unit: str, lines: int = 50,
                          ctx: RemoteContext | None = None) -> str:
    """Fetch journal logs for a unit, routing through the wrapper for non-root users."""
    if not ctx or not ctx.needs_sudo:
        return run_sudo_command(
            host, f"journalctl -u {shlex.quote(unit)} --no-pager -n {int(lines)}",
            ctx=ctx, timeout=30,
        )

    _require_privileged_helper(host, ctx)
    return run_remote_command(
        host, f"sudo {PRIVILEGED_HELPER_PATH} journalctl {shlex.quote(unit)} --lines {int(lines)}",
        timeout=30,
    )

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
    """Read a text file from the remote server.

    Raises FileNotFoundError if the file does not exist; RuntimeError on other failures.
    """
    buf = BytesIO()
    try:
        ok = host.get_file(remote_filename=path, filename_or_io=buf,
                           print_output=False, print_input=False)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise RuntimeError(f"Failed to read remote file: {path}: {e}") from e
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

# %% [markdown]
# ## Ports state (ports.json)
#
# Read-only accessor. All *mutations* of garden.json and ports.json go through
# the locked update functions below — an in-place overwrite would both need
# write permission on the file itself (the issue #18 ownership lockout) and
# silently lose concurrent updates.

# %%
#|export
def read_ports_state(host) -> dict:
    """Read port allocations from the host-level ports.json."""
    raw = read_remote_file(host, ports_path())
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted ports.json on server: {e}. You may need to re-run 'server init'.")

# %% [markdown]
# ## Directory upload
#
# Uses rsync via a shell command for efficiency.

# %%
#|export
def upload_directory(
    server: ServerConfig, local_path: str | Path, remote_path: str,
    exclude: list[str] | None = None, gitignore: bool = True,
) -> None:
    """Upload a local directory to the remote server using rsync.

    Uses the SSH agent when available (needed for encrypted keys).
    Falls back to specifying the key file directly.

    Parameters:
        exclude: Additional rsync exclude patterns.
        gitignore: If True (default), add ``--filter ':- .gitignore'`` so
            rsync honours .gitignore files in the source tree.
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

    extra_flags: list[str] = []
    if gitignore:
        extra_flags += ["--filter", ":- .gitignore"]
    if exclude:
        for pattern in exclude:
            extra_flags += ["--exclude", pattern]

    cmd = [
        "rsync", "-rlz", "--delete",
        *extra_flags,
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
# ## Locked state updates (compare-and-swap)
#
# Shared JSON state files (garden.json, the host ports registry, the tunnels
# active.json) are mutated by multiple clients on multiple machines. A plain
# "read under flock, mutate locally, write under flock" cycle releases the lock
# between the read and the write, so two concurrent writers can both read the
# same state and silently overwrite each other's update (e.g. the same port
# allocated twice).
#
# ``update_json_state_locked`` closes that gap with optimistic concurrency:
# the read captures the file's content hash (computed remotely, under flock),
# the mutation runs locally, and the write is a compare-and-swap — the staged
# temp file is only ``mv``-ed into place (again under flock) if the file's hash
# is unchanged. On a conflict the whole cycle retries with fresh state.
# Replacing via ``mv`` needs only directory write permission, so file
# ownership never matters (the issue #18 class of lockouts).

# %%
#|export
def _lock_path(ctx: RemoteContext | None = None) -> str:
    """Return the path to the remote lock file."""
    root = ctx.app_root if ctx else DEFAULT_APP_ROOT
    return f"{root}/.appgarden.lock"

def _raise_ports_permission_hint(e: Exception) -> None:
    """Re-raise a permission failure on the shared ports registry with a repair hint.

    The registry dir must be root:appgarden group-writable; older inits stamped
    it with whichever user ran them, locking out the box's other deploy users.
    """
    raise RuntimeError(
        f"Permission denied on the shared host ports registry ({HOST_STATE_DIR}). "
        f"Its ownership was likely set by an older 'server init' run as a different user. "
        f"Re-run 'appgarden server init <server>' (any entry for this box) to repair it."
    ) from e

def _is_permission_denied(e: Exception) -> bool:
    return isinstance(e, PermissionError) or "permission denied" in str(e).lower()

def _is_missing_file(e: Exception) -> bool:
    return isinstance(e, FileNotFoundError) or "no such file" in str(e).lower()

# %%
#|export
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

def _locked_read_json_with_sha(host, path: str, lock: str) -> tuple[str, str]:
    """Read a state file and its content hash atomically under flock.

    Returns ``(sha256_hex, raw_content)``. The hash is computed remotely by
    ``sha256sum`` so the later compare-and-swap compares like with like —
    a locally computed hash could disagree about trailing newlines the SSH
    transport normalises away.
    """
    q = shlex.quote
    inner = f"sha256sum {q(path)} | cut -d' ' -f1 && cat {q(path)}"
    out = run_remote_command(host, f"flock -w 10 {q(lock)} sh -c {q(inner)}")
    sha, _, raw = out.partition("\n")
    sha = sha.strip()
    if not _SHA256_RE.match(sha):
        raise RuntimeError(f"Unexpected sha256sum output reading {path}: {sha!r}")
    return sha, raw

def _cas_replace(host, path: str, lock: str, expected_sha: str | None, content: str) -> bool:
    """Atomically replace *path* with *content* iff it is unchanged (compare-and-swap).

    Stages *content* in a uniquely-named temp file, then — under flock — moves
    it into place only if the file's current hash still equals *expected_sha*
    (``None`` means the file must still be absent). Returns True on success,
    False if the file changed since it was read (the temp file is cleaned up).
    """
    q = shlex.quote
    tmp = f"{path}.tmp.{uuid.uuid4().hex[:8]}"
    write_remote_file(host, tmp, content)
    if expected_sha is None:
        cond = f"[ ! -e {q(path)} ]"
    else:
        # expected_sha is validated hex (see _locked_read_json_with_sha), safe to embed.
        cond = f"[ \"$(sha256sum {q(path)} | cut -d' ' -f1)\" = \"{expected_sha}\" ]"
    inner = (
        f"if {cond}; then mv {q(tmp)} {q(path)}; echo CAS_OK; "
        f"else rm -f {q(tmp)}; echo CAS_CONFLICT; fi"
    )
    out = run_remote_command(host, f"flock -w 10 {q(lock)} sh -c {q(inner)}")
    return "CAS_OK" in out

# %%
#|export
def update_json_state_locked(
    host,
    path: str,
    lock: str,
    mutate,
    *,
    default: dict | None = None,
    corrupt_hint: str | None = None,
    on_permission=None,
    retries: int = 5,
) -> dict:
    """Atomically apply ``mutate(state) -> state`` to a remote JSON state file.

    Read → mutate → compare-and-swap write, retrying the whole cycle with
    fresh state when a concurrent writer got there first, so no update is
    ever silently lost. *default* seeds the state when the file doesn't
    exist yet; without it a missing file is an error. *on_permission* is
    called with the original exception on permission failures (to raise a
    domain-specific hint). Exceptions raised by *mutate* propagate.
    """
    for _ in range(retries):
        state = None
        expected_sha: str | None = None
        try:
            expected_sha, raw = _locked_read_json_with_sha(host, path, lock)
        except (PermissionError, RuntimeError) as e:
            if _is_permission_denied(e):
                if on_permission is not None:
                    on_permission(e)
                raise
            if _is_missing_file(e):
                if default is None:
                    raise RuntimeError(
                        f"State file {path} not found on server. "
                        f"Run 'appgarden server init' first."
                    ) from e
                state = copy.deepcopy(default)
            else:
                raise
        if state is None:
            try:
                state = json.loads(raw)
            except json.JSONDecodeError as e:
                hint = corrupt_hint or f"Corrupted state file on server: {path}"
                raise RuntimeError(f"{hint} ({e})")
        state = mutate(state)
        content = json.dumps(state, indent=2)
        try:
            if _cas_replace(host, path, lock, expected_sha, content):
                return state
        except (PermissionError, RuntimeError) as e:
            if _is_permission_denied(e) and on_permission is not None:
                on_permission(e)
            raise
    raise RuntimeError(
        f"State file {path} kept changing under concurrent updates; "
        f"gave up after {retries} attempts."
    )

# %%
#|export
def update_garden_state_locked(host, mutate, ctx: RemoteContext | None = None) -> dict:
    """Apply *mutate* to garden.json with the lock held across read-modify-write."""
    path = garden_state_path(ctx)
    return update_json_state_locked(
        host, path, _lock_path(ctx), mutate,
        corrupt_hint=f"Corrupted garden.json on server: {path}. You may need to re-run 'server init'.",
    )

def update_ports_state_locked(host, mutate) -> dict:
    """Apply *mutate* to the host-level ports registry (shared across all gardens)."""
    return update_json_state_locked(
        host, ports_path(), HOST_PORTS_LOCK, mutate,
        corrupt_hint="Corrupted ports.json on server. You may need to re-run 'server init'.",
        on_permission=_raise_ports_permission_hint,
    )
