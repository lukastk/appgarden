# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp apps

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # App Lifecycle Management
#
# List, status, start, stop, restart, remove, redeploy, and logs
# for deployed applications.

# %%
#|export
import shlex
from dataclasses import dataclass

from rich.console import Console

from appgarden.config import ServerConfig
from appgarden.remote import (
    APPGARDEN_ROOT,
    RemoteContext, make_remote_context,
    ssh_connect, run_remote_command, write_remote_file,
    read_garden_state, write_garden_state, upload_directory,
    privileged_systemctl, privileged_remove_unit, privileged_journalctl,
)
from appgarden.routing import parse_url, remove_caddy_config
from appgarden.ports import release_port
from appgarden.deploy import _app_dir, _source_dir, _systemd_unit_name, is_git_url, SYSTEMD_UNIT_DIR

console = Console()

# %% [markdown]
# ## list_apps

# %%
#|export
@dataclass
class AppInfo:
    name: str
    method: str
    url: str
    routing: str
    port: int | None = None
    status: str | None = None

# %%
#|export
def list_apps(host, ctx: RemoteContext | None = None) -> list[AppInfo]:
    """List all apps from garden.json."""
    state = read_garden_state(host, ctx=ctx)
    apps = []
    for name, entry in state.get("apps", {}).items():
        apps.append(AppInfo(
            name=name,
            method=entry.get("method", "unknown"),
            url=entry.get("url", ""),
            routing=entry.get("routing", ""),
            port=entry.get("port"),
        ))
    return apps

# %%
#|export
def list_apps_with_status(host, ctx: RemoteContext | None = None) -> list[AppInfo]:
    """List all apps with live systemd status."""
    apps = list_apps(host, ctx=ctx)
    for app in apps:
        if app.method == "static":
            app.status = "serving"
        else:
            unit = _systemd_unit_name(app.name)
            try:
                result = privileged_systemctl(host, "is-active", unit, ctx=ctx)
                app.status = result.strip()
            except RuntimeError:
                app.status = "inactive"
    return apps

# %% [markdown]
# ## app_status

# %%
#|export
@dataclass
class AppStatus:
    name: str
    method: str
    url: str
    routing: str
    port: int | None
    status: str
    source: str | None = None
    source_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    meta: dict | None = None

# %%
#|export
def app_status(host, name: str, ctx: RemoteContext | None = None) -> AppStatus:
    """Get detailed status for a single app."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")

    entry = state["apps"][name]
    method = entry.get("method", "unknown")

    if method == "static":
        status = "serving"
    else:
        unit = _systemd_unit_name(name)
        try:
            result = privileged_systemctl(host, "is-active", unit, ctx=ctx)
            status = result.strip()
        except RuntimeError:
            status = "inactive"

    return AppStatus(
        name=name,
        method=method,
        url=entry.get("url", ""),
        routing=entry.get("routing", ""),
        port=entry.get("port"),
        status=status,
        source=entry.get("source"),
        source_type=entry.get("source_type"),
        created_at=entry.get("created_at"),
        updated_at=entry.get("updated_at"),
        meta=entry.get("meta"),
    )

# %% [markdown]
# ## App metadata

# %%
#|export
def get_app_metadata(host, name: str, ctx: RemoteContext | None = None) -> dict:
    """Read the ``meta`` dict from garden.json for an app."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")
    return state["apps"][name].get("meta", {})

# %%
#|export
def set_app_metadata(host, name: str, meta: dict, ctx: RemoteContext | None = None) -> None:
    """Replace the entire ``meta`` dict for an app."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")
    state["apps"][name]["meta"] = meta
    write_garden_state(host, state, ctx=ctx)

# %%
#|export
def update_app_metadata(host, name: str, updates: dict, ctx: RemoteContext | None = None) -> None:
    """Merge *updates* into the existing ``meta`` dict for an app."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")
    existing = state["apps"][name].get("meta", {})
    existing.update(updates)
    state["apps"][name]["meta"] = existing
    write_garden_state(host, state, ctx=ctx)

# %%
#|export
def remove_app_metadata_keys(host, name: str, keys: list[str], ctx: RemoteContext | None = None) -> None:
    """Delete specific keys from the ``meta`` dict for an app."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")
    existing = state["apps"][name].get("meta", {})
    for k in keys:
        existing.pop(k, None)
    state["apps"][name]["meta"] = existing
    write_garden_state(host, state, ctx=ctx)

# %% [markdown]
# ## start / stop / restart

# %%
#|export
def stop_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Stop an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "stop", unit, ctx=ctx)

# %%
#|export
def start_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Start an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "start", unit, ctx=ctx)

# %%
#|export
def restart_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Restart an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "restart", unit, ctx=ctx)

# %% [markdown]
# ## app_logs

# %%
#|export
def app_logs(host, name: str, lines: int = 50, follow: bool = False, ctx: RemoteContext | None = None) -> str:
    """Fetch logs for an app via journalctl.

    When *follow* is True, this would block — use for non-interactive
    retrieval only (follow is handled by the CLI via subprocess).
    Returns the log output as a string.
    """
    unit = _systemd_unit_name(name)
    return privileged_journalctl(host, unit, lines=lines, ctx=ctx)

# %% [markdown]
# ## remove_app
#
# Full cleanup: stop service, remove unit, caddy config, port, garden entry, files.

# %%
#|export
def remove_app(host, name: str, keep_data: bool = False, ctx: RemoteContext | None = None) -> None:
    """Remove an app and all its resources from the server."""
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")

    entry = state["apps"][name]
    method = entry.get("method", "unknown")
    url = entry.get("url", "")
    domain, path = parse_url(url)

    # 1. Stop and disable systemd service (if not static)
    if method != "static":
        unit = _systemd_unit_name(name)
        try:
            privileged_systemctl(host, "stop", unit, ctx=ctx)
        except RuntimeError:
            pass
        try:
            privileged_systemctl(host, "disable", unit, ctx=ctx)
        except RuntimeError:
            pass
        # Remove unit file
        privileged_remove_unit(host, unit, ctx=ctx)
        privileged_systemctl(host, "daemon-reload", ctx=ctx)

    # 2. Remove Caddy config
    remove_caddy_config(host, app_name=name, domain=domain, path=path,
                        garden_state=state, ctx=ctx)

    # 3. Release port
    if entry.get("port") is not None:
        try:
            release_port(host, name)
        except ValueError:
            pass

    # 4. Remove from garden.json
    del state["apps"][name]
    write_garden_state(host, state, ctx=ctx)

    # 5. Remove app files
    adir = _app_dir(name, ctx)
    if keep_data:
        # Remove everything except data/
        run_remote_command(host,
            f"find {shlex.quote(adir)} -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {{}} +")
    else:
        run_remote_command(host, f"rm -rf {shlex.quote(adir)}")

# %% [markdown]
# ## redeploy_app
#
# Re-upload/pull source, rebuild if Docker, restart service.

# %%
#|export
def redeploy_app(server: ServerConfig, host, name: str, ctx: RemoteContext | None = None) -> None:
    """Redeploy an app: update source, rebuild, restart."""
    if ctx is None:
        ctx = make_remote_context(server)
    state = read_garden_state(host, ctx=ctx)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")

    entry = state["apps"][name]
    method = entry.get("method", "unknown")
    source = entry.get("source")
    source_type = entry.get("source_type")
    source_path = _source_dir(name, ctx)

    # 1. Update source
    if source_type == "git":
        console.print("  [dim]Pulling latest changes...[/dim]")
        branch = entry.get("branch")
        if branch:
            run_remote_command(host, f"cd {shlex.quote(source_path)} && git pull origin {shlex.quote(branch)}", timeout=120)
        else:
            run_remote_command(host, f"cd {shlex.quote(source_path)} && git pull", timeout=120)
    elif source_type == "local" and source:
        console.print("  [dim]Re-uploading source...[/dim]")
        exclude = entry.get("exclude")
        gitignore = entry.get("gitignore", True)
        upload_directory(server, source, source_path, exclude=exclude, gitignore=gitignore)

    # 2. Rebuild Docker image if applicable
    if method in ("dockerfile", "auto"):
        image_name = f"appgarden-{name}"
        console.print("  [dim]Rebuilding Docker image...[/dim]")
        run_remote_command(host, f"docker build -t {shlex.quote(image_name)} {shlex.quote(source_path)}", timeout=600)

    # 3. Restart service (if not static)
    if method != "static":
        unit = _systemd_unit_name(name)
        console.print("  [dim]Restarting service...[/dim]")
        privileged_systemctl(host, "restart", unit, ctx=ctx)
    else:
        # Static: Caddy serves files directly, just reload
        privileged_systemctl(host, "reload", "caddy", ctx=ctx)

    # 4. Update timestamp
    from datetime import datetime, timezone
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["apps"][name] = entry
    write_garden_state(host, state, ctx=ctx)
