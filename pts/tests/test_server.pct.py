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
from appgarden.server import ping_server, init_server, CADDYFILE_CONTENT, CADDYFILE_TEMPLATE, SSH_HARDENING_CONTENT

# %% [markdown]
# ## ping_server

# %%
#|export
def _make_server():
    return ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
    )

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
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = ""
    host_mock.run_shell_command.return_value = (True, output_mock)
    host_mock.put_file.return_value = True

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    # Verify shell commands were run (apt update, docker, caddy, ufw, etc.)
    cmds = [c.kwargs.get("command", c.args[0] if c.args else "")
            for c in host_mock.run_shell_command.call_args_list]

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
    """init_server writes Caddyfile, SSH hardening, and state files."""
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = ""
    host_mock.run_shell_command.return_value = (True, output_mock)
    host_mock.put_file.return_value = True

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_server())

    # Collect all put_file calls: extract remote_filename from kwargs
    written_files = {}
    for c in host_mock.put_file.call_args_list:
        remote_path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written_files[remote_path] = bio.getvalue().decode("utf-8")

    assert "/etc/caddy/Caddyfile" in written_files
    assert "import" in written_files["/etc/caddy/Caddyfile"]

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
    """init_server with non-root user prefixes commands with sudo."""
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = ""
    host_mock.run_shell_command.return_value = (True, output_mock)
    host_mock.put_file.return_value = True

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_nonroot_server())

    cmds = [c.kwargs.get("command", c.args[0] if c.args else "")
            for c in host_mock.run_shell_command.call_args_list]

    # All privileged commands should have _sudo=True passed
    for c in host_mock.run_shell_command.call_args_list:
        assert c.kwargs.get("_sudo") is True, f"Expected _sudo=True for: {c.kwargs.get('command', '')}"

    # Should chown app root for non-root user
    assert any("chown" in c and "deploy" in c for c in cmds), "Should chown app root"
    # Should add user to docker group
    assert any("usermod" in c and "docker" in c for c in cmds), "Should add user to docker group"

# %%
#|export
def test_init_server_nonroot_writes_system_files_via_tmp():
    """init_server with non-root user writes system files via /tmp/ + sudo mv."""
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = ""
    host_mock.run_shell_command.return_value = (True, output_mock)
    host_mock.put_file.return_value = True

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(_make_nonroot_server())

    # Collect put_file paths
    put_paths = [c.kwargs.get("remote_filename", "") for c in host_mock.put_file.call_args_list]

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
    host_mock = MagicMock()
    output_mock = MagicMock()
    output_mock.stdout = ""
    host_mock.run_shell_command.return_value = (True, output_mock)
    host_mock.put_file.return_value = True

    with patch("appgarden.server.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host_mock)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        init_server(server)

    cmds = [c.kwargs.get("command", c.args[0] if c.args else "")
            for c in host_mock.run_shell_command.call_args_list]

    # Should create dirs under custom root
    assert any("/opt/myapps" in c and "mkdir" in c for c in cmds), "Should create custom root dirs"

    # Caddyfile should reference custom root
    written_files = {}
    for c in host_mock.put_file.call_args_list:
        remote_path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written_files[remote_path] = bio.getvalue().decode("utf-8")

    assert "/etc/caddy/Caddyfile" in written_files
    assert "/opt/myapps" in written_files["/etc/caddy/Caddyfile"]

    # State files should be at custom root
    assert "/opt/myapps/garden.json" in written_files
    assert "/opt/myapps/ports.json" in written_files
