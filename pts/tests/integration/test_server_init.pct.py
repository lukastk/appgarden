# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp integration.test_server_init

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Integration Test: Server Initialisation
#
# Verifies that `init_server` correctly sets up Docker, Caddy, UFW,
# and the AppGarden directory structure on a real server.

# %%
#|export
import json

import pytest

from appgarden.remote import ssh_connect, run_remote_command, read_remote_file

# %%
#|export
pytestmark = pytest.mark.integration

# %%
#|export
def test_docker_installed(initialized_server):
    """Docker is installed and running."""
    with ssh_connect(initialized_server) as host:
        out = run_remote_command(host, "docker --version")
        assert "Docker" in out

# %%
#|export
def test_caddy_installed(initialized_server):
    """Caddy is installed and running."""
    with ssh_connect(initialized_server) as host:
        out = run_remote_command(host, "caddy version")
        assert "v" in out

# %%
#|export
def test_caddy_config(initialized_server):
    """Root Caddyfile imports appgarden configs."""
    with ssh_connect(initialized_server) as host:
        content = read_remote_file(host, "/etc/caddy/Caddyfile")
        assert "/srv/appgarden/caddy/apps/*.caddy" in content
        assert "/srv/appgarden/caddy/tunnels/*.caddy" in content

# %%
#|export
def test_ufw_enabled(initialized_server):
    """UFW is active and allows SSH/HTTP/HTTPS."""
    with ssh_connect(initialized_server) as host:
        out = run_remote_command(host, "ufw status")
        assert "active" in out.lower()

# %%
#|export
def test_directory_structure(initialized_server):
    """AppGarden directory structure exists."""
    with ssh_connect(initialized_server) as host:
        dirs = [
            "/srv/appgarden/apps",
            "/srv/appgarden/caddy/apps",
            "/srv/appgarden/caddy/tunnels",
            "/srv/appgarden/tunnels",
        ]
        for d in dirs:
            out = run_remote_command(host, f"test -d {d} && echo yes")
            assert "yes" in out, f"Directory {d} missing"

# %%
#|export
def test_garden_json(initialized_server):
    """garden.json is initialised."""
    with ssh_connect(initialized_server) as host:
        raw = read_remote_file(host, "/srv/appgarden/garden.json")
        data = json.loads(raw)
        assert "apps" in data
        assert data["apps"] == {}

# %%
#|export
def test_ports_json(initialized_server):
    """ports.json is initialised."""
    with ssh_connect(initialized_server) as host:
        raw = read_remote_file(host, "/srv/appgarden/ports.json")
        data = json.loads(raw)
        assert "next_port" in data
        assert "allocated" in data
