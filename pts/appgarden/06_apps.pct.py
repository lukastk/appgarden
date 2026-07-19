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
    read_garden_state, update_garden_state_locked, upload_directory,
    privileged_systemctl, privileged_remove_unit, privileged_journalctl,
    systemctl_is_active,
)
from appgarden.routing import parse_url, remove_caddy_config
from appgarden.ports import release_port
from appgarden.deploy import _app_dir, _source_dir, _systemd_unit_name, _write_env_file, is_git_url, SYSTEMD_UNIT_DIR
from appgarden.routing import render_template

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
    repo: str | None = None

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
            repo=entry.get("repo"),
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
            # systemctl_is_active preserves the real state: a crashed unit
            # reports 'failed', not 'inactive' (issue #25)
            app.status = systemctl_is_active(host, _systemd_unit_name(app.name), ctx=ctx)
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
    repo: str | None = None
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
        # Preserves the real state: crashed units report 'failed' (issue #25)
        status = systemctl_is_active(host, _systemd_unit_name(name), ctx=ctx)

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
        repo=entry.get("repo"),
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
    def _mut(state: dict) -> dict:
        if name not in state.get("apps", {}):
            raise ValueError(f"App '{name}' not found")
        state["apps"][name]["meta"] = meta
        return state
    update_garden_state_locked(host, _mut, ctx=ctx)

# %%
#|export
def update_app_metadata(host, name: str, updates: dict, ctx: RemoteContext | None = None) -> None:
    """Merge *updates* into the existing ``meta`` dict for an app."""
    def _mut(state: dict) -> dict:
        if name not in state.get("apps", {}):
            raise ValueError(f"App '{name}' not found")
        existing = state["apps"][name].get("meta", {})
        existing.update(updates)
        state["apps"][name]["meta"] = existing
        return state
    update_garden_state_locked(host, _mut, ctx=ctx)

# %%
#|export
def remove_app_metadata_keys(host, name: str, keys: list[str], ctx: RemoteContext | None = None) -> None:
    """Delete specific keys from the ``meta`` dict for an app."""
    def _mut(state: dict) -> dict:
        if name not in state.get("apps", {}):
            raise ValueError(f"App '{name}' not found")
        existing = state["apps"][name].get("meta", {})
        for k in keys:
            existing.pop(k, None)
        state["apps"][name]["meta"] = existing
        return state
    update_garden_state_locked(host, _mut, ctx=ctx)

# %% [markdown]
# ## start / stop / restart

# %%
#|export
def _update_app_status(host, name: str, status: str, ctx: RemoteContext | None = None) -> None:
    """Update the status field for an app in garden.json (no-op if unregistered)."""
    def _mut(state: dict) -> dict:
        if name in state.get("apps", {}):
            state["apps"][name]["status"] = status
        return state
    update_garden_state_locked(host, _mut, ctx=ctx)

# %%
#|export
def stop_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Stop an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "stop", unit, ctx=ctx)
    _update_app_status(host, name, "inactive", ctx=ctx)

# %%
#|export
def start_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Start an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "start", unit, ctx=ctx)
    _update_app_status(host, name, "active", ctx=ctx)

# %%
#|export
def restart_app(host, name: str, ctx: RemoteContext | None = None) -> None:
    """Restart an app's systemd service."""
    unit = _systemd_unit_name(name)
    privileged_systemctl(host, "restart", unit, ctx=ctx)
    _update_app_status(host, name, "active", ctx=ctx)

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

    # 4. Remove from garden.json (pop, not del: a concurrent remove of the same
    # app just means the goal state — absent — is already reached)
    def _mut(fresh: dict) -> dict:
        fresh.get("apps", {}).pop(name, None)
        return fresh
    update_garden_state_locked(host, _mut, ctx=ctx)

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

    # 2. Rebuild Docker image and regenerate docker-compose.yml if applicable
    if method in ("dockerfile", "auto"):
        image_name = f"appgarden-{name}"
        console.print("  [dim]Rebuilding Docker image...[/dim]")
        run_remote_command(host, f"docker build -t {shlex.quote(image_name)} {shlex.quote(source_path)}", timeout=600)

        # Regenerate docker-compose.yml with stored settings
        adir = _app_dir(name, ctx)
        port = entry.get("port")
        container_port = entry.get("container_port", 3000)
        volumes = entry.get("volumes")
        try:
            run_remote_command(host, f"test -f {shlex.quote(adir + '/.env')}")
            env_file_flag = ".env"
        except RuntimeError:
            env_file_flag = None
        compose_content = render_template(
            "docker-compose.yml.j2",
            port=port,
            container_port=container_port,
            env_file=env_file_flag,
            volumes=volumes or None,
        )
        compose_content = compose_content.replace(
            "    build: .",
            f"    image: {image_name}",
        )
        write_remote_file(host, f"{adir}/docker-compose.yml", compose_content)

    # 3. Restart service (if not static)
    if method != "static":
        unit = _systemd_unit_name(name)
        console.print("  [dim]Restarting service...[/dim]")
        privileged_systemctl(host, "restart", unit, ctx=ctx)
    else:
        # Static: Caddy serves files directly, just reload
        privileged_systemctl(host, "reload", "caddy", ctx=ctx)

    # 4. Update timestamp and status (on fresh state, so concurrent changes
    # to other apps aren't clobbered by this slow operation's stale read)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    def _mut(fresh: dict) -> dict:
        fresh_entry = fresh.get("apps", {}).get(name)
        if fresh_entry is not None:
            fresh_entry["updated_at"] = now
            fresh_entry["status"] = "serving" if method == "static" else "active"
        return fresh
    update_garden_state_locked(host, _mut, ctx=ctx)
