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
from dataclasses import dataclass

from rich.console import Console

from appgarden.config import ServerConfig
from appgarden.remote import (
    APPGARDEN_ROOT,
    ssh_connect, run_remote_command, write_remote_file,
    read_garden_state, write_garden_state, upload_directory,
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
def list_apps(host) -> list[AppInfo]:
    """List all apps from garden.json."""
    state = read_garden_state(host)
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
def list_apps_with_status(host) -> list[AppInfo]:
    """List all apps with live systemd status."""
    apps = list_apps(host)
    for app in apps:
        if app.method == "static":
            app.status = "serving"
        else:
            unit = _systemd_unit_name(app.name)
            try:
                result = run_remote_command(host, f"systemctl is-active {unit}")
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

# %%
#|export
def app_status(host, name: str) -> AppStatus:
    """Get detailed status for a single app."""
    state = read_garden_state(host)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")

    entry = state["apps"][name]
    method = entry.get("method", "unknown")

    if method == "static":
        status = "serving"
    else:
        unit = _systemd_unit_name(name)
        try:
            result = run_remote_command(host, f"systemctl is-active {unit}")
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
    )

# %% [markdown]
# ## start / stop / restart

# %%
#|export
def stop_app(host, name: str) -> None:
    """Stop an app's systemd service."""
    unit = _systemd_unit_name(name)
    run_remote_command(host, f"systemctl stop {unit}")

# %%
#|export
def start_app(host, name: str) -> None:
    """Start an app's systemd service."""
    unit = _systemd_unit_name(name)
    run_remote_command(host, f"systemctl start {unit}")

# %%
#|export
def restart_app(host, name: str) -> None:
    """Restart an app's systemd service."""
    unit = _systemd_unit_name(name)
    run_remote_command(host, f"systemctl restart {unit}")

# %% [markdown]
# ## app_logs

# %%
#|export
def app_logs(host, name: str, lines: int = 50, follow: bool = False) -> str:
    """Fetch logs for an app via journalctl.

    When *follow* is True, this would block — use for non-interactive
    retrieval only (follow is handled by the CLI via subprocess).
    Returns the log output as a string.
    """
    unit = _systemd_unit_name(name)
    cmd = f"journalctl -u {unit} --no-pager -n {lines}"
    return run_remote_command(host, cmd, timeout=30)

# %% [markdown]
# ## remove_app
#
# Full cleanup: stop service, remove unit, caddy config, port, garden entry, files.

# %%
#|export
def remove_app(host, name: str, keep_data: bool = False) -> None:
    """Remove an app and all its resources from the server."""
    state = read_garden_state(host)
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
            run_remote_command(host, f"systemctl stop {unit}")
        except RuntimeError:
            pass
        try:
            run_remote_command(host, f"systemctl disable {unit}")
        except RuntimeError:
            pass
        # Remove unit file
        run_remote_command(host, f"rm -f {SYSTEMD_UNIT_DIR}/{unit}")
        run_remote_command(host, "systemctl daemon-reload")

    # 2. Remove Caddy config
    remove_caddy_config(host, app_name=name, domain=domain, path=path,
                        garden_state=state)

    # 3. Release port
    if entry.get("port") is not None:
        try:
            release_port(host, name)
        except ValueError:
            pass

    # 4. Remove from garden.json
    del state["apps"][name]
    write_garden_state(host, state)

    # 5. Remove app files
    app_dir = _app_dir(name)
    if keep_data:
        # Remove everything except data/
        run_remote_command(host,
            f"find {app_dir} -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {{}} +")
    else:
        run_remote_command(host, f"rm -rf {app_dir}")

# %% [markdown]
# ## redeploy_app
#
# Re-upload/pull source, rebuild if Docker, restart service.

# %%
#|export
def redeploy_app(server: ServerConfig, host, name: str) -> None:
    """Redeploy an app: update source, rebuild, restart."""
    state = read_garden_state(host)
    if name not in state.get("apps", {}):
        raise ValueError(f"App '{name}' not found")

    entry = state["apps"][name]
    method = entry.get("method", "unknown")
    source = entry.get("source")
    source_type = entry.get("source_type")
    source_path = _source_dir(name)

    # 1. Update source
    if source_type == "git":
        console.print("  [dim]Pulling latest changes...[/dim]")
        branch = entry.get("branch")
        if branch:
            run_remote_command(host, f"cd {source_path} && git pull origin {branch}", timeout=120)
        else:
            run_remote_command(host, f"cd {source_path} && git pull", timeout=120)
    elif source_type == "local" and source:
        console.print("  [dim]Re-uploading source...[/dim]")
        upload_directory(server, source, source_path)

    # 2. Rebuild Docker image if applicable
    if method in ("dockerfile", "auto"):
        image_name = f"appgarden-{name}"
        console.print("  [dim]Rebuilding Docker image...[/dim]")
        run_remote_command(host, f"docker build -t {image_name} {source_path}", timeout=600)

    # 3. Restart service (if not static)
    if method != "static":
        unit = _systemd_unit_name(name)
        console.print("  [dim]Restarting service...[/dim]")
        run_remote_command(host, f"systemctl restart {unit}")
    else:
        # Static: Caddy serves files directly, just reload
        run_remote_command(host, "systemctl reload caddy")

    # 4. Update timestamp
    from datetime import datetime, timezone
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["apps"][name] = entry
    write_garden_state(host, state)
