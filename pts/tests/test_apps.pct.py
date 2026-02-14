# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_apps

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # App Lifecycle Tests
#
# Unit tests for app listing, status, removal, and redeployment (mocked SSH).

# %%
#|export
import json
from unittest.mock import MagicMock, patch

from appgarden.config import ServerConfig
from appgarden.apps import (
    list_apps, list_apps_with_status, app_status,
    stop_app, start_app, restart_app,
    remove_app, redeploy_app,
    AppInfo, AppStatus,
)

# %%
#|export
def _make_server():
    return ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
    )

# %%
#|export
SAMPLE_GARDEN = {
    "apps": {
        "myapp": {
            "name": "myapp",
            "method": "dockerfile",
            "url": "myapp.apps.example.com",
            "routing": "subdomain",
            "port": 10000,
            "source": "/tmp/src",
            "source_type": "local",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        "docs": {
            "name": "docs",
            "method": "static",
            "url": "docs.apps.example.com",
            "routing": "subdomain",
            "source": "/tmp/docs",
            "source_type": "local",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    },
}

# %%
#|export
def _mock_host(garden_state=None, ports_state=None):
    """Create a mock host pre-loaded with garden and ports state."""
    if garden_state is None:
        garden_state = SAMPLE_GARDEN
    if ports_state is None:
        ports_state = {"next_port": 10001, "allocated": {"10000": "myapp"}}

    host = MagicMock()

    def _mock_run(command="", **kw):
        output = MagicMock()
        # Handle flock commands that read state files
        if "flock" in command and "cat" in command:
            if "ports.json" in command:
                output.stdout = json.dumps(ports_state)
            elif "garden.json" in command:
                output.stdout = json.dumps(garden_state)
            else:
                output.stdout = ""
        else:
            output.stdout = ""
        return (True, output)

    host.run_shell_command.side_effect = _mock_run
    host.put_file.return_value = True

    def _mock_get(remote_filename, filename_or_io, **kw):
        if "ports.json" in remote_filename:
            data = ports_state
        else:
            data = garden_state
        filename_or_io.write(json.dumps(data).encode("utf-8"))
        return True

    host.get_file.side_effect = _mock_get
    return host

# %% [markdown]
# ## list_apps

# %%
#|export
def test_list_apps():
    """list_apps returns AppInfo for each app in garden.json."""
    host = _mock_host()
    apps = list_apps(host)
    assert len(apps) == 2
    names = {a.name for a in apps}
    assert names == {"myapp", "docs"}

# %%
#|export
def test_list_apps_fields():
    """list_apps populates method, url, routing, port."""
    host = _mock_host()
    apps = list_apps(host)
    myapp = next(a for a in apps if a.name == "myapp")
    assert myapp.method == "dockerfile"
    assert myapp.url == "myapp.apps.example.com"
    assert myapp.routing == "subdomain"
    assert myapp.port == 10000

# %%
#|export
def test_list_apps_empty():
    """list_apps returns empty list when no apps deployed."""
    host = _mock_host(garden_state={"apps": {}})
    apps = list_apps(host)
    assert apps == []

# %% [markdown]
# ## app_status

# %%
#|export
def test_app_status_static():
    """app_status for static app returns 'serving' status."""
    host = _mock_host()
    status = app_status(host, "docs")
    assert status.name == "docs"
    assert status.method == "static"
    assert status.status == "serving"

# %%
#|export
def test_app_status_service():
    """app_status for non-static app checks systemctl."""
    host = _mock_host()
    # Wrap existing side_effect to also return "active" for systemctl
    original_side_effect = host.run_shell_command.side_effect
    def _mock_run(command="", **kw):
        if "systemctl is-active" in command:
            output = MagicMock()
            output.stdout = "active"
            return (True, output)
        return original_side_effect(command=command, **kw)
    host.run_shell_command.side_effect = _mock_run

    status = app_status(host, "myapp")
    assert status.name == "myapp"
    assert status.status == "active"
    assert status.port == 10000

# %%
#|export
def test_app_status_not_found():
    """app_status raises ValueError for unknown app."""
    import pytest
    host = _mock_host()
    with pytest.raises(ValueError, match="not found"):
        app_status(host, "nonexistent")

# %% [markdown]
# ## remove_app

# %%
#|export
def test_remove_app_cleans_up_all_resources():
    """remove_app stops service, removes caddy, releases port, removes from garden."""
    host = _mock_host()

    remove_app(host, "myapp")

    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]

    # Should stop and disable the systemd service
    assert any("systemctl stop" in c and "appgarden-myapp" in c for c in cmds)
    assert any("systemctl disable" in c and "appgarden-myapp" in c for c in cmds)

    # Should remove the unit file
    assert any("rm -f" in c and "appgarden-myapp.service" in c for c in cmds)

    # Should daemon-reload
    assert any("daemon-reload" in c for c in cmds)

    # Should remove app directory
    assert any("rm -rf" in c and "/srv/appgarden/apps/myapp" in c for c in cmds)

    # Should reload caddy
    assert any("reload caddy" in c for c in cmds)

    # Should have written updated garden.json without myapp
    written = {}
    for c in host.put_file.call_args_list:
        path = c.kwargs.get("remote_filename", "")
        bio = c.kwargs.get("filename_or_io")
        if bio and "garden.json" in path:
            written[path] = bio.getvalue().decode("utf-8")

    garden_writes = [v for k, v in written.items() if "garden.json" in k]
    assert len(garden_writes) >= 1
    # The last garden.json write should not contain myapp
    last_garden = json.loads(garden_writes[-1])
    assert "myapp" not in last_garden["apps"]

# %%
#|export
def test_remove_app_static():
    """remove_app for static apps skips systemd operations."""
    host = _mock_host()

    remove_app(host, "docs")

    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]

    # Should NOT try to stop a systemd service for static apps
    assert not any("systemctl stop" in c and "appgarden-docs" in c for c in cmds)

    # Should still remove caddy config and app files
    assert any("reload caddy" in c or "rm" in c for c in cmds)

# %%
#|export
def test_remove_app_keep_data():
    """remove_app with keep_data preserves the data/ directory."""
    host = _mock_host()

    remove_app(host, "myapp", keep_data=True)

    cmds = [c.kwargs.get("command", "") for c in host.run_shell_command.call_args_list]

    # Should use find to remove everything except data/
    assert any("find" in c and "! -name data" in c for c in cmds)
    # Should NOT rm -rf the entire app dir
    assert not any(c == "rm -rf /srv/appgarden/apps/myapp" for c in cmds)

# %%
#|export
def test_remove_app_not_found():
    """remove_app raises ValueError for unknown app."""
    import pytest
    host = _mock_host()
    with pytest.raises(ValueError, match="not found"):
        remove_app(host, "nonexistent")
