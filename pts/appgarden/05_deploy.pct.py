# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp deploy

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Deployment Logic
#
# Orchestrates deploying applications to remote servers.
# Phase 6 implements static-site deployment end-to-end;
# later phases add Docker and command methods.

# %%
#|export
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from appgarden.config import ServerConfig
from appgarden.remote import (
    APPGARDEN_ROOT,
    ssh_connect, run_remote_command, write_remote_file,
    read_garden_state, write_garden_state, upload_directory,
)
from appgarden.routing import parse_url, deploy_caddy_config

console = Console()

# %% [markdown]
# ## Source detection
#
# Determine whether a `--source` value is a local path or a git URL.

# %%
#|export
def is_git_url(source: str) -> bool:
    """Return True if *source* looks like a git URL."""
    return (
        source.startswith("https://")
        or source.startswith("http://")
        or source.startswith("git@")
        or source.startswith("git://")
        or source.endswith(".git")
    )

# %% [markdown]
# ## Source upload / clone

# %%
#|export
def _app_dir(name: str) -> str:
    """Return the remote app directory."""
    return f"{APPGARDEN_ROOT}/apps/{name}"

# %%
#|export
def _source_dir(name: str) -> str:
    """Return the remote source directory for an app."""
    return f"{_app_dir(name)}/source"

# %%
#|export
def upload_source(
    server: ServerConfig,
    host,
    name: str,
    source: str,
    branch: str | None = None,
) -> str:
    """Upload or clone source code to the remote server.

    Returns ``"local"`` or ``"git"`` indicating the source type.
    """
    remote_source = _source_dir(name)
    run_remote_command(host, f"mkdir -p {remote_source}")

    if is_git_url(source):
        branch_flag = f"-b {branch}" if branch else ""
        run_remote_command(
            host,
            f"rm -rf {remote_source} && git clone {branch_flag} {source} {remote_source}",
            timeout=120,
        )
        return "git"
    else:
        upload_directory(server, source, remote_source)
        return "local"

# %% [markdown]
# ## deploy_static
#
# Deploy a static site served directly by Caddy.
# No systemd service needed — Caddy serves the files.

# %%
#|export
def deploy_static(
    server: ServerConfig,
    name: str,
    source: str,
    url: str,
    branch: str | None = None,
) -> None:
    """Deploy a static site to the remote server.

    1. Upload/clone source
    2. Deploy Caddy config (file_server)
    3. Register in garden.json
    """
    domain, path = parse_url(url)
    console.print(f"[bold]Deploying static site[/bold] '{name}' → {url}")

    with ssh_connect(server) as host:
        # 1. Upload source
        console.print("  [dim]Uploading source...[/dim]")
        source_type = upload_source(server, host, name, source, branch)
        source_path = _source_dir(name)

        # 2. Deploy Caddy config
        console.print("  [dim]Configuring Caddy...[/dim]")
        garden_state = read_garden_state(host)
        deploy_caddy_config(
            host, app_name=name, domain=domain, path=path,
            method="static", source_path=source_path,
            garden_state=garden_state,
        )

        # 3. Register in garden.json
        now = datetime.now(timezone.utc).isoformat()
        app_entry = {
            "name": name,
            "method": "static",
            "url": url,
            "routing": "subdirectory" if path else "subdomain",
            "source_type": source_type,
            "source": source,
            "source_path": source_path,
            "created_at": now,
            "updated_at": now,
        }
        if branch:
            app_entry["branch"] = branch

        garden_state["apps"][name] = app_entry
        write_garden_state(host, garden_state)

        # 4. Write per-app metadata
        app_json_path = f"{_app_dir(name)}/app.json"
        write_remote_file(host, app_json_path, json.dumps(app_entry, indent=2))

    console.print(f"[bold green]Deployed '{name}' at {url}[/bold green]")
