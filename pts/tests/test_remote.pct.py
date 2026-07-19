# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_remote

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Remote Operations Tests
#
# Unit tests for remote helpers using mocks (no real SSH).

# %%
#|export
import json
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch, call

from appgarden.remote import (
    read_remote_file, write_remote_file, run_remote_command,
    read_garden_state, read_ports_state, upload_directory,
    update_garden_state_locked, update_ports_state_locked, update_json_state_locked,
    GARDEN_STATE_PATH, PORTS_PATH, HOST_STATE_DIR, PRIVILEGED_HELPER_PATH,
    DEFAULT_APP_ROOT, RemoteContext, make_remote_context,
    run_sudo_command, write_system_file,
    garden_state_path, ports_path, caddy_apps_dir, caddy_tunnels_dir,
    app_dir, source_dir, tunnels_state_path,
    check_privileged_helper,
    privileged_systemctl, privileged_install_unit,
    privileged_remove_unit, privileged_journalctl,
)
from appgarden.config import ServerConfig

# %% [markdown]
# ## read_remote_file / write_remote_file

# %%
#|export
def test_read_remote_file():
    """read_remote_file calls host.get_file and returns content."""
    host = MagicMock()
    host.get_file.side_effect = lambda remote_filename, filename_or_io, **kw: (
        filename_or_io.write(b"hello world") or True
    )

    result = read_remote_file(host, "/tmp/test.txt")
    assert result == "hello world"
    host.get_file.assert_called_once()

# %%
#|export
def test_read_remote_file_failure():
    """read_remote_file raises on failure."""
    import pytest
    host = MagicMock()
    host.get_file.return_value = False

    with pytest.raises(RuntimeError, match="Failed to read"):
        read_remote_file(host, "/tmp/missing.txt")

# %%
#|export
def test_write_remote_file():
    """write_remote_file calls host.put_file with a BytesIO."""
    host = MagicMock()
    host.put_file.return_value = True

    write_remote_file(host, "/tmp/out.txt", "content")
    host.put_file.assert_called_once()
    # Verify the content was passed via a BytesIO
    args = host.put_file.call_args
    bio = args.kwargs["filename_or_io"]
    assert bio.getvalue() == b"content"

# %%
#|export
def test_write_remote_file_failure():
    """write_remote_file raises on failure."""
    import pytest
    host = MagicMock()
    host.put_file.return_value = False

    with pytest.raises(RuntimeError, match="Failed to write"):
        write_remote_file(host, "/tmp/out.txt", "content")

# %% [markdown]
# ## run_remote_command

# %%
#|export
def test_run_remote_command():
    """run_remote_command returns stdout as a string."""
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "line1\nline2"
    host.run_shell_command.return_value = (True, output_mock)

    result = run_remote_command(host, "ls /tmp")
    assert result == "line1\nline2"

# %%
#|export
def test_run_remote_command_failure():
    """run_remote_command raises on command failure."""
    import pytest
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stderr = "error message"
    host.run_shell_command.return_value = (False, output_mock)

    with pytest.raises(RuntimeError, match="Remote command failed"):
        run_remote_command(host, "bad command")

# %% [markdown]
# ## Locked compare-and-swap state updates
#
# The updater must: hold the read and the write under flock with a hash
# compare-and-swap in between (so concurrent writers can't lose updates),
# stage content in a uniquely-named temp file moved into place with `mv`
# (so file ownership never matters — issue #18), retry on conflict, and
# seed from `default` when the file doesn't exist yet.

# %%
#|export
import hashlib

def _cas_host(initial: dict, *, missing: bool = False, conflicts: int = 0):
    """Mock host speaking the locked-read + CAS protocol for one state file.

    ``conflicts`` makes the first N CAS attempts report CAS_CONFLICT.
    ``missing`` makes reads fail like the file doesn't exist.
    """
    host = MagicMock()
    state_json = json.dumps(initial)
    remaining = {"conflicts": conflicts, "reads": 0}
    written: dict[str, str] = {}

    def _run(command="", **kw):
        out = MagicMock()
        out.stderr = ""
        if "CAS_OK" in command:
            if remaining["conflicts"] > 0:
                remaining["conflicts"] -= 1
                out.stdout = "CAS_CONFLICT"
            else:
                out.stdout = "CAS_OK"
            return (True, out)
        if "sha256sum" in command and "cat" in command:
            remaining["reads"] += 1
            if missing:
                out.stdout = ""
                out.stderr = "sha256sum: can't open file: No such file or directory"
                return (False, out)
            out.stdout = hashlib.sha256(state_json.encode()).hexdigest() + "\n" + state_json
            return (True, out)
        out.stdout = ""
        return (True, out)

    host.run_shell_command.side_effect = _run

    def _put(filename_or_io, remote_filename, **kw):
        written[remote_filename] = filename_or_io.getvalue().decode("utf-8")
        return True

    host.put_file.side_effect = _put
    host._written = written
    host._counters = remaining
    return host

# %%
#|export
def test_update_garden_state_applies_mutation():
    """The mutation lands, staged via a unique tmp file and a CAS mv."""
    host = _cas_host({"apps": {}})

    def add_app(state):
        state["apps"]["myapp"] = {"method": "static"}
        return state

    result = update_garden_state_locked(host, add_app)
    assert result["apps"]["myapp"] == {"method": "static"}

    # Staged to a uniquely-named tmp under the state path, never in place
    tmp_paths = list(host._written)
    assert len(tmp_paths) == 1
    assert tmp_paths[0].startswith(f"{GARDEN_STATE_PATH}.tmp.")
    assert json.loads(host._written[tmp_paths[0]])["apps"]["myapp"] == {"method": "static"}

    # The CAS command mv's the tmp into place under flock
    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    cas = [c for c in cmds if "CAS_OK" in c]
    assert len(cas) == 1
    assert "flock" in cas[0] and "mv" in cas[0] and GARDEN_STATE_PATH in cas[0]

# %%
#|export
def test_update_garden_state_with_ctx_path():
    """A custom app_root routes the update to that garden's files."""
    ctx = RemoteContext(app_root="/opt/garden")
    host = _cas_host({"apps": {}})
    update_garden_state_locked(host, lambda s: s, ctx=ctx)
    tmp_paths = list(host._written)
    assert tmp_paths[0].startswith("/opt/garden/garden.json.tmp.")

# %%
#|export
def test_update_state_retries_on_conflict():
    """A CAS conflict re-reads fresh state and retries the mutation."""
    host = _cas_host({"apps": {}}, conflicts=1)
    update_garden_state_locked(host, lambda s: s)
    # Two reads: initial + post-conflict retry
    assert host._counters["reads"] == 2
    # Two staged tmp files (one discarded by the failed CAS)
    assert len(host._written) == 2

# %%
#|export
def test_update_state_conflict_exhaustion_raises():
    """Persistent conflicts fail loudly instead of spinning forever."""
    import pytest
    host = _cas_host({"apps": {}}, conflicts=99)
    with pytest.raises(RuntimeError, match="kept changing"):
        update_garden_state_locked(host, lambda s: s)

# %%
#|export
def test_update_state_missing_file_seeds_default():
    """With a default, a missing state file is seeded and created via CAS."""
    host = _cas_host({}, missing=True)

    def add(state):
        state["tunnels"]["t1"] = {"url": "x"}
        return state

    result = update_json_state_locked(
        host, "/srv/appgarden/tunnels/active.json",
        "/srv/appgarden/tunnels/.tunnels.lock", add,
        default={"tunnels": {}},
    )
    assert result == {"tunnels": {"t1": {"url": "x"}}}
    # The create-CAS guards on the file still being absent
    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    cas = [c for c in cmds if "CAS_OK" in c]
    assert len(cas) == 1 and "! -e" in cas[0]

# %%
#|export
def test_update_state_missing_file_without_default_raises():
    """Without a default, a missing state file is a loud error pointing at init."""
    import pytest
    host = _cas_host({}, missing=True)
    with pytest.raises(RuntimeError, match="server init"):
        update_garden_state_locked(host, lambda s: s)

# %%
#|export
def test_update_state_mutation_error_propagates():
    """Exceptions from the mutation (e.g. app-not-found) propagate unchanged."""
    import pytest
    host = _cas_host({"apps": {}})

    def boom(state):
        raise ValueError("App 'ghost' not found")

    with pytest.raises(ValueError, match="ghost"):
        update_garden_state_locked(host, boom)

# %% [markdown]
# ## Locked ports state: permission-denied hint (issue #18)
#
# When the shared host ports registry is unwritable (its ownership was stamped
# by an older `server init` run as a different user), the raw failure is an
# opaque `Permission denied` deep in pyinfra/flock. The locked updater
# translates it into an error that points at re-running `server init`.

# %%
#|export
def test_update_ports_state_sftp_permission_hint():
    """An SFTP PermissionError staging the registry tmp file becomes a hint to
    re-run server init."""
    import pytest
    host = _cas_host({"next_port": 10000, "allocated": {}})
    host.put_file.side_effect = PermissionError(13, "Permission denied")

    with pytest.raises(RuntimeError) as excinfo:
        update_ports_state_locked(host, lambda p: p)
    assert HOST_STATE_DIR in str(excinfo.value)
    assert "server init" in str(excinfo.value)

# %%
#|export
def test_update_ports_state_read_permission_hint():
    """A 'Permission denied' on the locked read gets the hint."""
    import pytest
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stderr = "flock: cannot open lock file /var/lib/appgarden/.ports.lock: Permission denied"
    host.run_shell_command.return_value = (False, output_mock)

    with pytest.raises(RuntimeError, match="server init"):
        update_ports_state_locked(host, lambda p: p)

# %%
#|export
def test_update_ports_state_cas_permission_hint():
    """A 'Permission denied' from the CAS mv also gets the hint."""
    import pytest
    host = _cas_host({"next_port": 10000, "allocated": {}})
    inner = host.run_shell_command.side_effect

    def _run(command="", **kw):
        if "CAS_OK" in command:
            out = MagicMock()
            out.stdout = ""
            out.stderr = "mv: cannot move tmp: Permission denied"
            return (False, out)
        return inner(command=command, **kw)

    host.run_shell_command.side_effect = _run
    with pytest.raises(RuntimeError, match="server init"):
        update_ports_state_locked(host, lambda p: p)

# %%
#|export
def test_update_ports_state_other_errors_unchanged():
    """Non-permission failures keep the original error, not the hint."""
    import pytest
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stderr = "flock: timeout while waiting to get lock"
    host.run_shell_command.return_value = (False, output_mock)

    with pytest.raises(RuntimeError, match="Remote command failed"):
        update_ports_state_locked(host, lambda p: p)

# %% [markdown]
# ## RemoteContext

# %%
#|export
def test_remote_context_defaults():
    """RemoteContext has sensible defaults."""
    ctx = RemoteContext()
    assert ctx.app_root == DEFAULT_APP_ROOT
    assert ctx.needs_sudo is False

# %%
#|export
def test_make_remote_context_root():
    """make_remote_context for root user: no sudo needed."""
    server = ServerConfig(ssh_user="root", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    ctx = make_remote_context(server)
    assert ctx.app_root == DEFAULT_APP_ROOT
    assert ctx.needs_sudo is False

# %%
#|export
def test_make_remote_context_nonroot():
    """make_remote_context for non-root user: needs sudo."""
    server = ServerConfig(ssh_user="deploy", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    ctx = make_remote_context(server)
    assert ctx.needs_sudo is True

# %%
#|export
def test_make_remote_context_custom_app_root():
    """make_remote_context respects custom app_root."""
    server = ServerConfig(
        ssh_user="deploy", ssh_key="~/.ssh/id_rsa", domain="example.com",
        host="1.2.3.4", app_root="/opt/myapps",
    )
    ctx = make_remote_context(server)
    assert ctx.app_root == "/opt/myapps"
    assert ctx.needs_sudo is True

# %% [markdown]
# ## Path functions

# %%
#|export
def test_path_functions_default():
    """Path functions return default paths when ctx is None."""
    assert garden_state_path() == f"{DEFAULT_APP_ROOT}/garden.json"
    # ports.json is host-global (box-level), not under app_root
    assert ports_path() == PORTS_PATH == "/var/lib/appgarden/ports.json"
    assert caddy_apps_dir() == f"{DEFAULT_APP_ROOT}/caddy/apps"
    assert caddy_tunnels_dir() == f"{DEFAULT_APP_ROOT}/caddy/tunnels"
    assert app_dir(None, "myapp") == f"{DEFAULT_APP_ROOT}/apps/myapp"
    assert source_dir(None, "myapp") == f"{DEFAULT_APP_ROOT}/apps/myapp/source"
    assert tunnels_state_path() == f"{DEFAULT_APP_ROOT}/tunnels/active.json"

# %%
#|export
def test_path_functions_custom_root():
    """Per-garden path functions use custom app_root from ctx; ports stay host-global."""
    ctx = RemoteContext(app_root="/opt/garden")
    assert garden_state_path(ctx) == "/opt/garden/garden.json"
    # ports.json is host-global — independent of app_root
    assert ports_path() == "/var/lib/appgarden/ports.json"
    assert caddy_apps_dir(ctx) == "/opt/garden/caddy/apps"
    assert caddy_tunnels_dir(ctx) == "/opt/garden/caddy/tunnels"
    assert app_dir(ctx, "foo") == "/opt/garden/apps/foo"
    assert source_dir(ctx, "foo") == "/opt/garden/apps/foo/source"
    assert tunnels_state_path(ctx) == "/opt/garden/tunnels/active.json"

# %% [markdown]
# ## run_sudo_command

# %%
#|export
def test_run_sudo_command_no_sudo():
    """run_sudo_command without sudo does not pass _sudo."""
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "ok"
    host.run_shell_command.return_value = (True, output_mock)

    result = run_sudo_command(host, "apt-get update")
    kwargs = host.run_shell_command.call_args.kwargs
    assert kwargs["command"] == "apt-get update"
    assert "_sudo" not in kwargs
    assert result == "ok"

# %%
#|export
def test_run_sudo_command_with_sudo():
    """run_sudo_command with needs_sudo passes _sudo=True to pyinfra."""
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "ok"
    host.run_shell_command.return_value = (True, output_mock)

    ctx = RemoteContext(needs_sudo=True)
    result = run_sudo_command(host, "apt-get update", ctx=ctx)
    kwargs = host.run_shell_command.call_args.kwargs
    assert kwargs["command"] == "apt-get update"
    assert kwargs["_sudo"] is True

# %%
#|export
def test_run_sudo_command_with_chain():
    """run_sudo_command passes _sudo for && chains too."""
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "ok"
    host.run_shell_command.return_value = (True, output_mock)

    ctx = RemoteContext(needs_sudo=True)
    run_sudo_command(host, "apt-get update && apt-get upgrade -y", ctx=ctx)
    kwargs = host.run_shell_command.call_args.kwargs
    assert kwargs["command"] == "apt-get update && apt-get upgrade -y"
    assert kwargs["_sudo"] is True

# %%
#|export
def test_run_sudo_command_no_ctx():
    """run_sudo_command with ctx=None does not pass _sudo."""
    host = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "ok"
    host.run_shell_command.return_value = (True, output_mock)

    run_sudo_command(host, "systemctl reload caddy", ctx=None)
    kwargs = host.run_shell_command.call_args.kwargs
    assert kwargs["command"] == "systemctl reload caddy"
    assert "_sudo" not in kwargs

# %% [markdown]
# ## write_system_file

# %%
#|export
def test_write_system_file_no_sudo():
    """write_system_file without sudo writes directly via put_file."""
    host = MagicMock()
    host.put_file.return_value = True

    write_system_file(host, "/etc/caddy/Caddyfile", "content")
    host.put_file.assert_called_once()
    kwargs = host.put_file.call_args.kwargs
    assert kwargs["remote_filename"] == "/etc/caddy/Caddyfile"
    assert "_sudo" not in kwargs

# %%
#|export
def test_write_system_file_with_sudo():
    """write_system_file with sudo passes _sudo=True to put_file."""
    host = MagicMock()
    host.put_file.return_value = True

    ctx = RemoteContext(needs_sudo=True)
    write_system_file(host, "/etc/caddy/Caddyfile", "content", ctx=ctx)

    host.put_file.assert_called_once()
    kwargs = host.put_file.call_args.kwargs
    assert kwargs["remote_filename"] == "/etc/caddy/Caddyfile"
    assert kwargs["_sudo"] is True
    assert kwargs["filename_or_io"].getvalue() == b"content"

# %% [markdown]
# ## Garden state read with custom ctx

# %%
#|export
def test_read_garden_state_with_ctx():
    """read_garden_state uses the custom path from ctx."""
    ctx = RemoteContext(app_root="/opt/garden")
    host = MagicMock()
    host.get_file.side_effect = lambda remote_filename, filename_or_io, **kw: (
        filename_or_io.write(b'{"apps": {}}') or True
    )
    state = read_garden_state(host, ctx=ctx)
    get_path = host.get_file.call_args.kwargs["remote_filename"]
    assert get_path == "/opt/garden/garden.json"
    assert state == {"apps": {}}

# %% [markdown]
# ## check_privileged_helper

# %%
#|export
def test_check_privileged_helper_installed():
    """check_privileged_helper returns True when wrapper exists."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.return_value = (True, output_mock)

    assert check_privileged_helper(host) is True

# %%
#|export
def test_check_privileged_helper_missing():
    """check_privileged_helper returns False when wrapper missing."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stderr = ""
    host.run_shell_command.return_value = (False, output_mock)

    assert check_privileged_helper(host) is False

# %% [markdown]
# ## privileged_systemctl

# %%
#|export
def test_privileged_systemctl_root_passthrough():
    """For root (needs_sudo=False), privileged_systemctl calls run_sudo_command directly."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = "active"
    host.run_shell_command.return_value = (True, output_mock)

    ctx = RemoteContext(needs_sudo=False)
    result = privileged_systemctl(host, "is-active", "appgarden-myapp.service", ctx=ctx)
    assert result == "active"
    cmd = host.run_shell_command.call_args.kwargs["command"]
    assert cmd.startswith("systemctl is-active")
    assert "_sudo" not in host.run_shell_command.call_args.kwargs

# %%
#|export
def test_privileged_systemctl_root_no_ctx():
    """With ctx=None, privileged_systemctl runs directly without sudo."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = "ok"
    host.run_shell_command.return_value = (True, output_mock)

    result = privileged_systemctl(host, "daemon-reload", ctx=None)
    assert result == "ok"
    cmd = host.run_shell_command.call_args.kwargs["command"]
    assert cmd == "systemctl daemon-reload"

# %%
#|export
def test_privileged_systemctl_nonroot_wrapper():
    """For non-root, privileged_systemctl routes through wrapper."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = "active"
    # First call: check wrapper exists (test -x); second call: actual command
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),   # check_privileged_helper
        (True, output_mock),             # actual systemctl via wrapper
    ]

    ctx = RemoteContext(needs_sudo=True)
    result = privileged_systemctl(host, "restart", "appgarden-myapp.service", ctx=ctx)
    assert result == "active"
    # The second call should use the wrapper path
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert PRIVILEGED_HELPER_PATH in cmd
    assert "systemctl restart" in cmd

# %%
#|export
def test_privileged_systemctl_nonroot_daemon_reload():
    """Non-root daemon-reload routes through wrapper without unit name."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),   # check_privileged_helper
        (True, output_mock),
    ]

    ctx = RemoteContext(needs_sudo=True)
    privileged_systemctl(host, "daemon-reload", ctx=ctx)
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert "systemctl daemon-reload" in cmd
    assert PRIVILEGED_HELPER_PATH in cmd

# %%
#|export
def test_privileged_systemctl_nonroot_caddy_reload():
    """Non-root caddy reload routes through wrapper."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),
        (True, output_mock),
    ]

    ctx = RemoteContext(needs_sudo=True)
    privileged_systemctl(host, "reload", "caddy", ctx=ctx)
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert "systemctl reload caddy" in cmd

# %%
#|export
def test_privileged_systemctl_nonroot_no_wrapper():
    """Non-root without wrapper raises helpful error."""
    import pytest
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stderr = ""
    host.run_shell_command.return_value = (False, output_mock)

    ctx = RemoteContext(needs_sudo=True)
    with pytest.raises(RuntimeError, match="Re-run.*server init"):
        privileged_systemctl(host, "restart", "appgarden-myapp.service", ctx=ctx)

# %% [markdown]
# ## privileged_install_unit

# %%
#|export
def test_privileged_install_unit_root():
    """For root, privileged_install_unit writes directly via put_file."""
    host = MagicMock()
    host.put_file.return_value = True

    ctx = RemoteContext(needs_sudo=False)
    privileged_install_unit(host, "appgarden-myapp.service", "[Unit]\nDescription=test", ctx=ctx)
    host.put_file.assert_called_once()
    path = host.put_file.call_args.kwargs["remote_filename"]
    assert path == "/etc/systemd/system/appgarden-myapp.service"

# %%
#|export
def test_privileged_install_unit_nonroot():
    """For non-root, privileged_install_unit writes temp file then calls wrapper."""
    host = MagicMock()
    host.put_file.return_value = True
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),   # check_privileged_helper
        (True, output_mock),             # wrapper call
    ]

    ctx = RemoteContext(needs_sudo=True)
    privileged_install_unit(host, "appgarden-myapp.service", "[Unit]\nDescription=test", ctx=ctx)

    # Temp file written via put_file, at an unpredictable (random-suffix) path
    # so another local user can't pre-create it (issue #21)
    import re as _re
    host.put_file.assert_called_once()
    tmp_path = host.put_file.call_args.kwargs["remote_filename"]
    assert _re.fullmatch(r"/tmp/appgarden-unit-[0-9a-f]{32}\.tmp", tmp_path), tmp_path

    # Wrapper called with install-unit
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert "install-unit" in cmd
    assert PRIVILEGED_HELPER_PATH in cmd

# %% [markdown]
# ## privileged_remove_unit

# %%
#|export
def test_privileged_remove_unit_root():
    """For root, privileged_remove_unit uses rm -f directly."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.return_value = (True, output_mock)

    ctx = RemoteContext(needs_sudo=False)
    privileged_remove_unit(host, "appgarden-myapp.service", ctx=ctx)
    cmd = host.run_shell_command.call_args.kwargs["command"]
    assert "rm -f" in cmd
    assert "appgarden-myapp.service" in cmd

# %%
#|export
def test_privileged_remove_unit_nonroot():
    """For non-root, privileged_remove_unit routes through wrapper."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = ""
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),   # check_privileged_helper
        (True, output_mock),
    ]

    ctx = RemoteContext(needs_sudo=True)
    privileged_remove_unit(host, "appgarden-myapp.service", ctx=ctx)
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert "remove-unit" in cmd
    assert PRIVILEGED_HELPER_PATH in cmd

# %% [markdown]
# ## privileged_journalctl

# %%
#|export
def test_privileged_journalctl_root():
    """For root, privileged_journalctl runs journalctl directly."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = "log line 1\nlog line 2"
    host.run_shell_command.return_value = (True, output_mock)

    ctx = RemoteContext(needs_sudo=False)
    result = privileged_journalctl(host, "appgarden-myapp.service", lines=100, ctx=ctx)
    assert result == "log line 1\nlog line 2"
    cmd = host.run_shell_command.call_args.kwargs["command"]
    assert "journalctl -u" in cmd

# %%
#|export
def test_privileged_journalctl_nonroot():
    """For non-root, privileged_journalctl routes through wrapper."""
    host = MagicMock()
    output_mock = MagicMock(); output_mock.stdout = "log line"
    host.run_shell_command.side_effect = [
        (True, MagicMock(stdout="")),   # check_privileged_helper
        (True, output_mock),
    ]

    ctx = RemoteContext(needs_sudo=True)
    result = privileged_journalctl(host, "appgarden-myapp.service", lines=25, ctx=ctx)
    assert result == "log line"
    cmd = host.run_shell_command.call_args_list[1].kwargs["command"]
    assert "journalctl" in cmd
    assert "--lines 25" in cmd
    assert PRIVILEGED_HELPER_PATH in cmd

# %% [markdown]
# ## upload_directory exclude/gitignore

# %%
#|export
from appgarden.remote import upload_directory
import subprocess as _subprocess

def test_upload_directory_gitignore_default():
    """upload_directory includes --filter ':- .gitignore' by default."""
    server = ServerConfig(ssh_user="root", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    with patch.object(_subprocess, "run") as mock_run:
        upload_directory(server, "/tmp/src", "/srv/appgarden/apps/myapp/source")
    cmd = mock_run.call_args[0][0]
    assert "--filter" in cmd
    idx = cmd.index("--filter")
    assert cmd[idx + 1] == ":- .gitignore"

# %%
#|export
def test_upload_directory_no_gitignore():
    """upload_directory omits .gitignore filter when gitignore=False."""
    server = ServerConfig(ssh_user="root", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    with patch.object(_subprocess, "run") as mock_run:
        upload_directory(server, "/tmp/src", "/srv/appgarden/apps/myapp/source", gitignore=False)
    cmd = mock_run.call_args[0][0]
    assert "--filter" not in cmd

# %%
#|export
def test_upload_directory_exclude_patterns():
    """upload_directory adds --exclude flags for each pattern."""
    server = ServerConfig(ssh_user="root", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    with patch.object(_subprocess, "run") as mock_run:
        upload_directory(server, "/tmp/src", "/srv/appgarden/apps/myapp/source",
                         exclude=["node_modules", ".env"], gitignore=False)
    cmd = mock_run.call_args[0][0]
    # Find all --exclude flags
    excludes = []
    for i, arg in enumerate(cmd):
        if arg == "--exclude" and i + 1 < len(cmd):
            excludes.append(cmd[i + 1])
    assert excludes == ["node_modules", ".env"]

# %%
#|export
def test_upload_directory_exclude_and_gitignore():
    """upload_directory with both gitignore and excludes places filter before excludes."""
    server = ServerConfig(ssh_user="root", ssh_key="~/.ssh/id_rsa", domain="example.com", host="1.2.3.4")
    with patch.object(_subprocess, "run") as mock_run:
        upload_directory(server, "/tmp/src", "/srv/appgarden/apps/myapp/source",
                         exclude=[".venv"], gitignore=True)
    cmd = mock_run.call_args[0][0]
    filter_idx = cmd.index("--filter")
    exclude_idx = cmd.index("--exclude")
    assert filter_idx < exclude_idx, "gitignore filter should come before exclude patterns"

# %% [markdown]
# ## upload_directory error hints

# %%
#|export
def _upload_server():
    return ServerConfig(ssh_user="deploy", ssh_key="~/.ssh/id_rsa",
                        domain="apps.example.com", host="1.2.3.4")

# %%
#|export
def test_upload_directory_rsync_missing(tmp_path):
    """A missing rsync binary raises an install hint."""
    import pytest
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="'rsync' is not installed"):
            upload_directory(_upload_server(), str(tmp_path), "/srv/appgarden/apps/x/source")

# %%
#|export
def test_upload_directory_ssh_failure_hints_ssh_agent(tmp_path):
    """rsync exit 255 (SSH failure) points at ssh-agent for encrypted keys."""
    import pytest
    import subprocess as _subprocess
    err = _subprocess.CalledProcessError(255, ["rsync"], output="", stderr="Connection closed")
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="ssh-agent"):
            upload_directory(_upload_server(), str(tmp_path), "/srv/appgarden/apps/x/source")

# %%
#|export
def test_upload_directory_permission_denied_hints_init(tmp_path):
    """rsync exit 23 with a permission error points at server init / chown."""
    import pytest
    import subprocess as _subprocess
    err = _subprocess.CalledProcessError(
        23, ["rsync"], output="",
        stderr='rsync: mkstemp failed: Permission denied (13)')
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="server init --include group"):
            upload_directory(_upload_server(), str(tmp_path), "/srv/appgarden/apps/x/source")

# %%
#|export
def test_upload_directory_generic_failure(tmp_path):
    """Other rsync failures surface the exit code and stderr."""
    import pytest
    import subprocess as _subprocess
    err = _subprocess.CalledProcessError(11, ["rsync"], output="", stderr="disk full")
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match=r"rsync failed \(exit 11\): disk full"):
            upload_directory(_upload_server(), str(tmp_path), "/srv/appgarden/apps/x/source")

# %% [markdown]
# ## Corrupted state file reads

# %%
#|export
def test_read_garden_state_corrupted():
    """Corrupt garden.json raises the re-run-init hint, not a raw JSON error."""
    import pytest
    host = MagicMock()
    host.get_file.side_effect = lambda remote_filename, filename_or_io, **kw: (
        filename_or_io.write(b"{not valid json") or True
    )
    with pytest.raises(RuntimeError, match="Corrupted garden.json"):
        read_garden_state(host)

# %%
#|export
def test_read_ports_state_corrupted():
    """Corrupt ports.json raises the re-run-init hint."""
    import pytest
    host = MagicMock()
    host.get_file.side_effect = lambda remote_filename, filename_or_io, **kw: (
        filename_or_io.write(b"[1, 2,") or True
    )
    with pytest.raises(RuntimeError, match="Corrupted ports.json"):
        read_ports_state(host)
