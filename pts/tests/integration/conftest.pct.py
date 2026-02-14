# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp integration.conftest

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Integration Test Fixtures
#
# Provisions a Hetzner Cloud server for integration tests.
# Requires a `.env` file in the repo root (see `.env.sample`).

# %%
#|export
import json
import os
import random
import string
import subprocess
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from appgarden.config import ServerConfig
from appgarden.server import init_server

# %%
#|export
# Load .env from repo root
_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path)

# %%
#|export
def _rand_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

# %%
#|export
def _hcloud(ctx: str, *args: str) -> str:
    """Run an hcloud CLI command and return stdout."""
    cmd = ["hcloud", "--context", ctx, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()

# %%
#|export
def _delete_server(ctx: str, name: str) -> None:
    """Delete a server, ignoring errors (best-effort cleanup)."""
    try:
        _hcloud(ctx, "server", "delete", name)
    except Exception:
        pass  # already gone or unreachable — nothing we can do

# %% [markdown]
# ## Cleanup stale test servers
#
# Run automatically at the start of every integration session.
# Finds any leftover `appgarden-test-*` servers and deletes them.

# %%
#|export
def cleanup_stale_servers() -> None:
    """Delete any leftover ``appgarden-test-*`` servers from previous runs."""
    ctx = os.environ.get("APPGARDEN_TEST_HCLOUD_CONTEXT")
    if not ctx:
        return
    try:
        raw = _hcloud(ctx, "server", "list", "-o", "json")
        servers = json.loads(raw)
        for srv in servers:
            if srv["name"].startswith("appgarden-test-"):
                print(f"Cleaning up stale test server: {srv['name']}")
                _delete_server(ctx, srv["name"])
    except Exception:
        pass  # hcloud not available or no permissions — skip silently

# %% [markdown]
# ## hcloud_server fixture
#
# Session-scoped: provisions a server once, tears it down at the end.
# Wrapped in try/finally so the server is deleted even if tests crash.

# %%
#|export
@pytest.fixture(scope="session")
def hcloud_server():
    """Provision a Hetzner Cloud server and yield its IP. Tears down on exit."""
    # Clean up any leftovers from previous aborted runs
    cleanup_stale_servers()

    ctx = os.environ["APPGARDEN_TEST_HCLOUD_CONTEXT"]
    ssh_key_name = os.environ["APPGARDEN_TEST_HCLOUD_SSH_KEY"]
    server_type = os.environ.get("APPGARDEN_TEST_SERVER_TYPE", "cx22")
    location = os.environ.get("APPGARDEN_TEST_LOCATION", "fsn1")
    image = os.environ.get("APPGARDEN_TEST_IMAGE", "ubuntu-24.04")

    name = f"appgarden-test-{_rand_suffix()}"

    # Create server
    _hcloud(ctx, "server", "create",
            "--name", name,
            "--type", server_type,
            "--location", location,
            "--image", image,
            "--ssh-key", ssh_key_name)

    try:
        # Wait for running status and get IP
        ip = None
        for _ in range(60):
            raw = _hcloud(ctx, "server", "describe", name, "-o", "json")
            info = json.loads(raw)
            status = info.get("status")
            if status == "running":
                ip = info["public_net"]["ipv4"]["ip"]
                break
            time.sleep(2)

        if ip is None:
            pytest.fail(f"Server '{name}' did not reach running state")

        # Wait for SSH to become available
        time.sleep(20)

        yield {"name": name, "ip": ip, "context": ctx}

    finally:
        # Always delete, even on Ctrl+C or crash
        _delete_server(ctx, name)

# %% [markdown]
# ## initialized_server fixture
#
# Depends on `hcloud_server`; runs `init_server` and yields a `ServerConfig`.

# %%
#|export
@pytest.fixture(scope="session")
def initialized_server(hcloud_server):
    """Run init_server on the provisioned server and yield a ServerConfig."""
    ssh_key_path = os.environ.get("APPGARDEN_TEST_SSH_KEY_PATH", "~/.ssh/id_rsa")
    domain = "test.example.com"  # dummy domain for tests

    srv = ServerConfig(
        ssh_user="root",
        ssh_key=ssh_key_path,
        domain=domain,
        host=hcloud_server["ip"],
    )
    init_server(srv)
    return srv
