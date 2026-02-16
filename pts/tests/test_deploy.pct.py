# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_deploy

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Deploy Tests
#
# Unit tests for deployment logic (mocked SSH).

# %%
#|export
import json
from unittest.mock import MagicMock, patch, call

from appgarden.config import ServerConfig
from appgarden.deploy import (
    is_git_url,
    deploy_static,
    deploy_command,
    deploy_docker_compose,
    deploy_dockerfile,
    upload_source,
    _app_dir,
    _source_dir,
    _write_env_file,
)

# %% [markdown]
# ## is_git_url

# %%
#|export
def test_is_git_url_https():
    """HTTPS git URLs are detected."""
    assert is_git_url("https://github.com/user/repo.git") is True
    assert is_git_url("https://github.com/user/repo") is True

# %%
#|export
def test_is_git_url_ssh():
    """SSH git URLs are detected."""
    assert is_git_url("git@github.com:user/repo.git") is True

# %%
#|export
def test_is_git_url_git_protocol():
    """git:// protocol URLs are detected."""
    assert is_git_url("git://example.com/repo.git") is True

# %%
#|export
def test_is_git_url_local_path():
    """Local paths are not git URLs."""
    assert is_git_url("/home/user/project") is False
    assert is_git_url("./my-site") is False
    assert is_git_url("../build") is False

# %% [markdown]
# ## deploy_static

# %%
#|export
def _make_server():
    return ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
    )

# %%
#|export
def _mock_host():
    """Create a mock host with standard return values."""
    host = MagicMock()

    def _mock_run(command="", **kw):
        output = MagicMock()
        # Handle flock commands that read state files
        if "flock" in command and "cat" in command:
            if "ports.json" in command:
                output.stdout = json.dumps({"next_port": 10000, "allocated": {}})
            elif "garden.json" in command:
                output.stdout = json.dumps({"apps": {}})
            else:
                output.stdout = ""
        else:
            output.stdout = ""
        return (True, output)

    host.run_shell_command.side_effect = _mock_run
    host.put_file.return_value = True

    def _mock_get(remote_filename, filename_or_io, **kw):
        if "ports.json" in remote_filename:
            data = {"next_port": 10000, "allocated": {}}
        else:
            data = {"apps": {}}
        filename_or_io.write(json.dumps(data).encode("utf-8"))
        return True

    host.get_file.side_effect = _mock_get
    return host

# %%
#|export
def test_deploy_static_subdomain():
    """deploy_static uploads source, writes Caddy config, and registers app."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory") as mock_upload:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(_make_server(), "mysite", "/tmp/site", "mysite.apps.example.com")

    # Should have uploaded the directory
    mock_upload.assert_called_once()

    # Collect all written files
    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[path] = bio.getvalue().decode("utf-8")

    # Should have written a Caddy config
    caddy_files = [p for p in written if p.endswith(".caddy")]
    assert len(caddy_files) == 1
    caddy_content = written[caddy_files[0]]
    assert "mysite.apps.example.com" in caddy_content
    assert "file_server" in caddy_content

    # Should have written garden.json
    garden_files = [p for p in written if "garden.json" in p]
    assert len(garden_files) == 1
    garden = json.loads(written[garden_files[0]])
    assert "mysite" in garden["apps"]
    assert garden["apps"]["mysite"]["method"] == "static"
    assert garden["apps"]["mysite"]["url"] == "mysite.apps.example.com"

    # Should have written app.json
    app_json_files = [p for p in written if "app.json" in p]
    assert len(app_json_files) == 1

# %%
#|export
def test_deploy_static_subdirectory():
    """deploy_static with subdirectory URL creates correct routing."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory") as mock_upload:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(_make_server(), "docs", "/tmp/docs", "apps.example.com/docs")

    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[path] = bio.getvalue().decode("utf-8")

    # Check garden.json has subdirectory routing
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["docs"]["routing"] == "subdirectory"

    # Caddy config should use handle_path
    caddy_files = [p for p in written if p.endswith(".caddy")]
    assert len(caddy_files) == 1
    caddy_content = written[caddy_files[0]]
    assert "handle_path /docs/*" in caddy_content

# %%
#|export
def test_deploy_static_git_source():
    """deploy_static with git URL clones instead of uploading."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(
            _make_server(), "mysite",
            "https://github.com/user/site.git",
            "mysite.apps.example.com",
        )

    # Should have run git clone
    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    assert any("git clone" in c for c in cmds), "Should git clone the source"

    # Check garden state records source_type=git
    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[path] = bio.getvalue().decode("utf-8")

    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["mysite"]["source_type"] == "git"

# %%
#|export
def test_deploy_static_git_with_branch():
    """deploy_static with git URL and branch passes -b flag."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(
            _make_server(), "mysite",
            "https://github.com/user/site.git",
            "mysite.apps.example.com",
            branch="gh-pages",
        )

    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    clone_cmds = [c for c in cmds if "git clone" in c]
    assert len(clone_cmds) == 1
    assert "-b" in clone_cmds[0] and "gh-pages" in clone_cmds[0]

# %%
#|export
def test_deploy_static_registers_in_garden():
    """deploy_static writes app entry to garden.json with correct fields."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(_make_server(), "blog", "/tmp/blog", "blog.apps.example.com")

    # Find the garden.json write
    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[path] = bio.getvalue().decode("utf-8")

    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])

    app = garden["apps"]["blog"]
    assert app["name"] == "blog"
    assert app["method"] == "static"
    assert app["url"] == "blog.apps.example.com"
    assert app["routing"] == "subdomain"
    assert app["source_type"] == "local"
    assert "created_at" in app
    assert "updated_at" in app

# %% [markdown]
# ## Helper to extract written files from mock

# %%
#|export
def _get_written_files(host):
    """Extract all files written via put_file on a mock host."""
    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio:
            written[path] = bio.getvalue().decode("utf-8")
    return written

# %% [markdown]
# ## deploy_command

# %%
#|export
def test_deploy_command_creates_systemd_unit():
    """deploy_command creates a systemd unit with PORT env var."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_command(
            _make_server(), "myapp", "python app.py",
            "myapp.apps.example.com", source="/tmp/src",
        )

    written = _get_written_files(host)

    # Should have a systemd unit
    unit_files = [p for p in written if p.endswith(".service")]
    assert len(unit_files) == 1
    unit_content = written[unit_files[0]]
    assert "python app.py" in unit_content
    assert '"PORT=10000"' in unit_content

    # Should have registered with method=command
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["myapp"]["method"] == "command"
    assert garden["apps"]["myapp"]["port"] == 10000

# %%
#|export
def test_deploy_command_with_env():
    """deploy_command writes .env file with correct permissions."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_command(
            _make_server(), "myapp", "node server.js",
            "myapp.apps.example.com",
            env_vars={"SECRET": "abc123"},
        )

    written = _get_written_files(host)

    # Should have written .env file
    env_files = [p for p in written if p.endswith("/.env")]
    assert len(env_files) == 1
    assert 'SECRET="abc123"' in written[env_files[0]]

    # Should have created file with restrictive permissions
    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    assert any("install -m 600" in c for c in cmds)

# %% [markdown]
# ## deploy_docker_compose

# %%
#|export
def test_deploy_docker_compose_creates_systemd():
    """deploy_docker_compose creates systemd unit with docker compose up."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_docker_compose(
            _make_server(), "mystack", "/tmp/compose-project",
            "mystack.apps.example.com",
        )

    written = _get_written_files(host)

    unit_files = [p for p in written if p.endswith(".service")]
    assert len(unit_files) == 1
    unit_content = written[unit_files[0]]
    assert "docker compose up" in unit_content
    assert "docker compose down" in unit_content
    assert "docker.service" in unit_content

    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["mystack"]["method"] == "docker-compose"

# %% [markdown]
# ## deploy_dockerfile

# %%
#|export
def test_deploy_dockerfile_builds_and_creates_compose():
    """deploy_dockerfile builds image and generates docker-compose.yml."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_dockerfile(
            _make_server(), "webapp", "/tmp/app",
            "webapp.apps.example.com",
            container_port=8080,
        )

    # Should have run docker build
    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]
    assert any("docker build" in c and "appgarden-webapp" in c for c in cmds)

    written = _get_written_files(host)

    # Should have generated docker-compose.yml with image reference
    compose_files = [p for p in written if "docker-compose.yml" in p]
    assert len(compose_files) == 1
    compose_content = written[compose_files[0]]
    assert "appgarden-webapp" in compose_content
    assert "10000:8080" in compose_content

    # Should register with method=dockerfile
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["webapp"]["method"] == "dockerfile"
    assert garden["apps"]["webapp"]["container_port"] == 8080

# %% [markdown]
# ## Metadata flows through deploy

# %%
#|export
def test_deploy_static_with_meta():
    """deploy_static passes meta through to garden.json via _register_app."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(
            _make_server(), "mysite", "/tmp/site",
            "mysite.apps.example.com",
            meta={"team": "frontend", "tier": "free"},
        )

    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["mysite"]["meta"] == {"team": "frontend", "tier": "free"}

# %%
#|export
def test_deploy_static_without_meta():
    """deploy_static without meta does not add a meta key."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(_make_server(), "mysite", "/tmp/site", "mysite.apps.example.com")

    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert "meta" not in garden["apps"]["mysite"]

# %%
#|export
def test_deploy_command_with_meta():
    """deploy_command passes meta through to garden.json."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_command(
            _make_server(), "myapp", "python app.py",
            "myapp.apps.example.com",
            meta={"visibility": "internal"},
        )

    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["myapp"]["meta"] == {"visibility": "internal"}

# %% [markdown]
# ## Exclude/gitignore flows through deploy

# %%
#|export
def test_deploy_static_sets_status_serving():
    """deploy_static sets status to 'serving' in garden.json."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(_make_server(), "mysite", "/tmp/site", "mysite.apps.example.com")

    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["mysite"]["status"] == "serving"

# %%
#|export
def test_deploy_command_sets_status_active():
    """deploy_command sets status to 'active' in garden.json."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_command(
            _make_server(), "myapp", "python app.py",
            "myapp.apps.example.com",
        )

    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["myapp"]["status"] == "active"

# %%
#|export
def test_deploy_dockerfile_with_volumes():
    """deploy_dockerfile passes volumes to docker-compose.yml and stores in garden.json."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory"):
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_dockerfile(
            _make_server(), "webapp", "/tmp/app",
            "webapp.apps.example.com",
            volumes=["./data:/app/data", "/var/logs:/app/logs:ro"],
        )

    written = _get_written_files(host)

    # docker-compose.yml should contain the volume mounts
    compose_files = [p for p in written if "docker-compose.yml" in p]
    assert len(compose_files) == 1
    compose_content = written[compose_files[0]]
    assert "./data:/app/data" in compose_content
    assert "/var/logs:/app/logs:ro" in compose_content

    # garden.json should store the volumes
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["webapp"]["volumes"] == ["./data:/app/data", "/var/logs:/app/logs:ro"]

# %%
#|export
def test_deploy_static_with_exclude():
    """deploy_static passes exclude/gitignore to rsync and stores in garden.json."""
    host = _mock_host()

    with patch("appgarden.deploy.ssh_connect") as mock_connect, \
         patch("appgarden.deploy.upload_directory") as mock_upload:
        mock_connect.return_value.__enter__ = MagicMock(return_value=host)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        deploy_static(
            _make_server(), "mysite", "/tmp/site",
            "mysite.apps.example.com",
            exclude=["node_modules", ".env"],
            gitignore=False,
        )

    # upload_directory should have been called with exclude and gitignore
    mock_upload.assert_called_once()
    call_kwargs = mock_upload.call_args
    assert call_kwargs.kwargs.get("exclude") == ["node_modules", ".env"]
    assert call_kwargs.kwargs.get("gitignore") is False

    # garden.json should have exclude and gitignore stored
    written = _get_written_files(host)
    garden_files = [p for p in written if "garden.json" in p]
    garden = json.loads(written[garden_files[0]])
    assert garden["apps"]["mysite"]["exclude"] == ["node_modules", ".env"]
    assert garden["apps"]["mysite"]["gitignore"] is False
