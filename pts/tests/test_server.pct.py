# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_server

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Server Tests
#
# Unit tests for server init and ping (mocked SSH).

# %%
#|export
from unittest.mock import patch, MagicMock, call

from appgarden.config import ServerConfig
from appgarden.server import (
    ping_server, init_server, INIT_STEPS,
    CADDYFILE_CONTENT, CADDYFILE_TEMPLATE, CADDYFILE_MARKER_BEGIN, CADDYFILE_MARKER_END,
    SSH_HARDENING_CONTENT,
)

# %% [markdown]
# ## Helpers

# %%
#|export
def _make_server():
    return ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
    )

# %%
#|export
def _make_host_mock(*, state_files_exist=True, caddyfile_content=""):
    """Create a host mock with configurable behavior for test -f and cat commands."""
    host_mock = MagicMock()
    ok_output = MagicMock()
    ok_output.stdout = ""
    fail_output = MagicMock()
    fail_output.stderr = ""

    cat_output = MagicMock()
    cat_output.stdout = caddyfile_content

    def run_shell_side_effect(**kwargs):
        cmd = kwargs.get("command", "")
        if cmd.startswith("test -f") and not state_files_exist:
            return (False, fail_output)
        if cmd.startswith("cat /etc/caddy/Caddyfile"):
            return (True, cat_output)
        return (True, ok_output)

    host_mock.run_shell_command.side_effect = run_shell_side_effect
    host_mock.put_file.return_value = True
    host_mock.get_file.return_value = True
    return host_mock

# %%
#|export
def _get_written_files(host_mock):
    """Extract {path: content} from put_file calls."""
    written = {}
    for c in host_mock.put_file.call_args_list:
        remote_path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[remote_path] = bio.getvalue().decode("utf-8")
    return written

# %%
#|export
def _get_cmds(host_mock):
    """Extract command strings from run_shell_command calls."""
    return [c.kwargs.get("command", c.args[0] if c.args else "")
            for c in host_mock.run_shell_command.call_args_list]

# %% [markdown]
# ## ping_server

# %%
#|export
def test_ping_server_success():
    """ping_server returns True when SSH succeeds."""
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = "ok"
    host_mock.run_shell_command.return_value = (True, output_mock)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        assert ping_server(_make_server()) is True

# %%
#|export
def test_ping_server_failure():
    """ping_server returns False when SSH connection fails."""
    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.side_effect = Exception("Connection refused")
        assert ping_server(_make_server()) is False

# %% [markdown]
# ## init_server

# %%
#|export
def test_init_server_runs_expected_commands():
    """init_server executes the expected sequence of operations."""
    host_mock = _make_host_mock()

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    cmds = _get_cmds(host_mock)

    # Should include key setup steps
    assert any("apt-get update" in c for c in cmds), "Should run apt update"
    assert any("docker-ce" in c for c in cmds), "Should install Docker"
    assert any("caddy" in c for c in cmds), "Should install Caddy"
    assert any("ufw" in c for c in cmds), "Should configure UFW"
    assert any("fail2ban" in c for c in cmds), "Should install fail2ban"
    assert any("unattended-upgrades" in c for c in cmds), "Should setup auto-updates"
    assert any("mkdir" in c for c in cmds), "Should create directories"

# %%
#|export
def test_init_server_writes_config_files():
    """init_server writes Caddyfile (with markers), SSH hardening, and state files."""
    host_mock = _make_host_mock(state_files_exist=False)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    written_files = _get_written_files(host_mock)

    # Caddyfile should contain marker block with import lines
    assert "/etc/caddy/Caddyfile" in written_files
    caddyfile = written_files["/etc/caddy/Caddyfile"]
    assert CADDYFILE_MARKER_BEGIN in caddyfile
    assert CADDYFILE_MARKER_END in caddyfile
    assert "import" in caddyfile

    assert "/etc/ssh/sshd_config.d/hardening.conf" in written_files
    assert "PasswordAuthentication no" in written_files["/etc/ssh/sshd_config.d/hardening.conf"]

    assert "/srv/appgarden/garden.json" in written_files
    assert "/srv/appgarden/ports.json" in written_files

# %% [markdown]
# ## init_server with non-root user

# %%
#|export
def _make_nonroot_server():
    return ServerConfig(
        ssh_user="deploy", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
    )

# %%
#|export
def test_init_server_nonroot_uses_sudo():
    """init_server with non-root user prefixes privileged commands with sudo."""
    host_mock = _make_host_mock()

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_nonroot_server())

    cmds = _get_cmds(host_mock)

    # Privileged commands should have _sudo=True; non-privileged (test -f) should not
    for c in host_mock.run_shell_command.call_args_list:
        cmd = c.kwargs.get("command", "")
        if cmd.startswith("test -f"):
            continue  # state file existence check runs without sudo
        assert c.kwargs.get("_sudo") is True, f"Expected _sudo=True for: {cmd}"

    # Should chown app root for non-root user
    assert any("chown" in c and "deploy" in c for c in cmds), "Should chown app root"
    # Should add user to docker group
    assert any("usermod" in c and "docker" in c for c in cmds), "Should add user to docker group"

# %%
#|export
def test_init_server_nonroot_writes_system_files_via_tmp():
    """init_server with non-root user writes system files via /tmp/ + sudo mv."""
    host_mock = _make_host_mock()

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_nonroot_server())

    # System files (/etc/) should be written via put_file with _sudo=True
    etc_calls = [c for c in host_mock.put_file.call_args_list
                 if c.kwargs.get("remote_filename", "").startswith("/etc/")]
    assert len(etc_calls) >= 2, f"Should write at least 2 system files, got: {[c.kwargs.get('remote_filename') for c in etc_calls]}"
    for c in etc_calls:
        assert c.kwargs.get("_sudo") is True, f"System file write should have _sudo=True: {c.kwargs.get('remote_filename')}"

# %%
#|export
def test_init_server_custom_app_root():
    """init_server with custom app_root uses the custom path."""
    server = ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
        app_root="/opt/myapps",
    )
    host_mock = _make_host_mock(state_files_exist=False)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(server)

    cmds = _get_cmds(host_mock)

    # Should create dirs under custom root
    assert any("/opt/myapps" in c and "mkdir" in c for c in cmds), "Should create custom root dirs"

    # Caddyfile should reference custom root within marker block
    written_files = _get_written_files(host_mock)
    assert "/etc/caddy/Caddyfile" in written_files
    caddyfile = written_files["/etc/caddy/Caddyfile"]
    assert "/opt/myapps" in caddyfile
    assert CADDYFILE_MARKER_BEGIN in caddyfile

    # State files should be at custom root
    assert "/opt/myapps/garden.json" in written_files
    assert "/opt/myapps/ports.json" in written_files

# %% [markdown]
# ## Additive Caddyfile

# %%
#|export
def test_init_server_caddyfile_additive():
    """Existing Caddyfile content is preserved, block appended."""
    existing = "example.com {\n    respond \"Hello\"\n}\n"
    host_mock = _make_host_mock(caddyfile_content=existing)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    written_files = _get_written_files(host_mock)
    caddyfile = written_files["/etc/caddy/Caddyfile"]

    # Existing content preserved
    assert 'respond "Hello"' in caddyfile
    # Marker block appended
    assert CADDYFILE_MARKER_BEGIN in caddyfile
    assert CADDYFILE_MARKER_END in caddyfile
    assert "import /srv/appgarden/caddy/apps/*.caddy" in caddyfile

# %%
#|export
def test_init_server_caddyfile_idempotent():
    """Running init twice doesn't duplicate the marker block."""
    # Simulate a Caddyfile that already has the marker block
    existing = (
        "example.com {\n    respond \"Hello\"\n}\n\n"
        f"{CADDYFILE_MARKER_BEGIN}\n"
        "import /srv/appgarden/caddy/apps/*.caddy\n"
        "import /srv/appgarden/caddy/tunnels/*.caddy\n"
        f"{CADDYFILE_MARKER_END}\n"
    )
    host_mock = _make_host_mock(caddyfile_content=existing)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    written_files = _get_written_files(host_mock)
    caddyfile = written_files["/etc/caddy/Caddyfile"]

    # Should have exactly one marker block
    assert caddyfile.count(CADDYFILE_MARKER_BEGIN) == 1
    assert caddyfile.count(CADDYFILE_MARKER_END) == 1
    # Existing content still preserved
    assert 'respond "Hello"' in caddyfile

# %% [markdown]
# ## State file preservation

# %%
#|export
def test_init_server_preserves_existing_state_files():
    """Existing garden.json is not overwritten."""
    host_mock = _make_host_mock(state_files_exist=True)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    written_files = _get_written_files(host_mock)
    # State files should NOT be written when they already exist
    assert "/srv/appgarden/garden.json" not in written_files
    assert "/srv/appgarden/ports.json" not in written_files

# %%
#|export
def test_init_server_creates_state_files_when_missing():
    """Missing state files are created."""
    host_mock = _make_host_mock(state_files_exist=False)

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    written_files = _get_written_files(host_mock)
    assert "/srv/appgarden/garden.json" in written_files
    assert "/srv/appgarden/ports.json" in written_files

# %% [markdown]
# ## Step skipping

# %%
#|export
def test_init_server_skip_steps():
    """skip={'docker', 'firewall'} skips those commands."""
    host_mock = _make_host_mock()

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server(), skip={"docker", "firewall"})

    cmds = _get_cmds(host_mock)

    # Skipped steps should not appear
    assert not any("docker-ce" in c for c in cmds), "Docker install should be skipped"
    assert not any("ufw" in c for c in cmds), "UFW should be skipped"

    # Non-skipped steps should still run
    assert any("apt-get update" in c for c in cmds), "Should still run apt update"
    assert any("caddy" in c for c in cmds), "Should still install Caddy"
    assert any("mkdir" in c for c in cmds), "Should still create directories"

# %%
#|export
def test_init_server_minimal():
    """skip=INIT_STEPS only runs essential steps."""
    host_mock = _make_host_mock()

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server(), skip=set(INIT_STEPS))

    cmds = _get_cmds(host_mock)

    # All optional steps should be skipped
    assert not any("apt-get update" in c and "upgrade" in c for c in cmds), "System update should be skipped"
    assert not any("docker-ce" in c for c in cmds), "Docker install should be skipped"
    assert not any("ufw" in c for c in cmds), "UFW should be skipped"
    assert not any("fail2ban" in c for c in cmds), "fail2ban should be skipped"
    assert not any("unattended-upgrades" in c for c in cmds), "unattended-upgrades should be skipped"

    # Essential steps should still run
    assert any("mkdir" in c for c in cmds), "Should create directories"
    assert any("caddy" in c and "systemctl" in c for c in cmds), "Should start Caddy"

    # Caddyfile should still be written (essential)
    written_files = _get_written_files(host_mock)
    assert "/etc/caddy/Caddyfile" in written_files

    # SSH hardening should NOT be written (optional)
    assert "/etc/ssh/sshd_config.d/hardening.conf" not in written_files
