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
    read_garden_state, write_garden_state,
    read_ports_state, write_ports_state,
    GARDEN_STATE_PATH, PORTS_PATH,
    DEFAULT_APP_ROOT, RemoteContext, make_remote_context,
    run_sudo_command, write_system_file,
    garden_state_path, ports_path, caddy_apps_dir, caddy_tunnels_dir,
    app_dir, source_dir, tunnels_state_path,
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
# ## Garden state round-trip

# %%
#|export
def test_garden_state_roundtrip():
    """read/write garden state serialises JSON correctly."""
    state_data = {"apps": {"myapp": {"name": "myapp", "method": "static"}}}
    written_bytes = None

    host = MagicMock()

    # Capture what write_garden_state sends (BytesIO)
    def mock_put(filename_or_io, remote_filename, **kw):
        nonlocal written_bytes
        written_bytes = filename_or_io.getvalue()
        return True
    host.put_file.side_effect = mock_put

    write_garden_state(host, state_data)
    assert written_bytes is not None
    assert json.loads(written_bytes.decode("utf-8")) == state_data

    # Now mock read to return what was written (bytes into BytesIO)
    def mock_get(remote_filename, filename_or_io, **kw):
        filename_or_io.write(written_bytes)
        return True
    host.get_file.side_effect = mock_get

    loaded = read_garden_state(host)
    assert loaded == state_data

# %% [markdown]
# ## Ports state round-trip

# %%
#|export
def test_ports_state_roundtrip():
    """read/write ports state serialises JSON correctly."""
    ports_data = {"next_port": 10002, "allocated": {"10000": "app1", "10001": "app2"}}
    written_bytes = None

    host = MagicMock()

    def mock_put(filename_or_io, remote_filename, **kw):
        nonlocal written_bytes
        written_bytes = filename_or_io.getvalue()
        return True
    host.put_file.side_effect = mock_put

    write_ports_state(host, ports_data)
    assert json.loads(written_bytes.decode("utf-8")) == ports_data

    def mock_get(remote_filename, filename_or_io, **kw):
        filename_or_io.write(written_bytes)
        return True
    host.get_file.side_effect = mock_get

    loaded = read_ports_state(host)
    assert loaded == ports_data

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
    assert ports_path() == f"{DEFAULT_APP_ROOT}/ports.json"
    assert caddy_apps_dir() == f"{DEFAULT_APP_ROOT}/caddy/apps"
    assert caddy_tunnels_dir() == f"{DEFAULT_APP_ROOT}/caddy/tunnels"
    assert app_dir(None, "myapp") == f"{DEFAULT_APP_ROOT}/apps/myapp"
    assert source_dir(None, "myapp") == f"{DEFAULT_APP_ROOT}/apps/myapp/source"
    assert tunnels_state_path() == f"{DEFAULT_APP_ROOT}/tunnels/active.json"

# %%
#|export
def test_path_functions_custom_root():
    """Path functions use custom app_root from ctx."""
    ctx = RemoteContext(app_root="/opt/garden")
    assert garden_state_path(ctx) == "/opt/garden/garden.json"
    assert ports_path(ctx) == "/opt/garden/ports.json"
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
# ## Garden/ports state with custom ctx

# %%
#|export
def test_garden_state_with_ctx():
    """read/write garden state uses custom path from ctx."""
    ctx = RemoteContext(app_root="/opt/garden")
    host = MagicMock()

    # Write
    host.put_file.return_value = True
    write_garden_state(host, {"apps": {}}, ctx=ctx)
    put_path = host.put_file.call_args.kwargs["remote_filename"]
    assert put_path == "/opt/garden/garden.json"

    # Read
    host.get_file.side_effect = lambda remote_filename, filename_or_io, **kw: (
        filename_or_io.write(b'{"apps": {}}') or True
    )
    state = read_garden_state(host, ctx=ctx)
    get_path = host.get_file.call_args.kwargs["remote_filename"]
    assert get_path == "/opt/garden/garden.json"
    assert state == {"apps": {}}
