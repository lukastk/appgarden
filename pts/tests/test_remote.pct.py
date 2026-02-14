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
)

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
