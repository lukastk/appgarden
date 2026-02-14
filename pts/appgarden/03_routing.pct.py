# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp routing

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Caddy Routing
#
# URL parsing, Caddy config generation, and deployment/removal of
# per-app `.caddy` files on the remote server.

# %%
#|export
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from appgarden.remote import (
    APPGARDEN_ROOT,
    ssh_connect, read_remote_file, write_remote_file,
    run_remote_command, read_garden_state,
)
from appgarden.config import ServerConfig

# %%
#|export
CADDY_APPS_DIR = f"{APPGARDEN_ROOT}/caddy/apps"
CADDY_TUNNELS_DIR = f"{APPGARDEN_ROOT}/caddy/tunnels"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

# %% [markdown]
# ## parse_url
#
# Splits a URL into `(domain, path_or_none)`.
#
# - `myapp.apps.example.com` → `("myapp.apps.example.com", None)` — subdomain
# - `apps.example.com/myapp` → `("apps.example.com", "myapp")` — subdirectory

# %%
#|export
def parse_url(url: str) -> tuple[str, str | None]:
    """Parse a URL into (domain, path_or_none).

    Returns the path component (without leading slash) if the URL
    contains a ``/``, indicating subdirectory routing.  Otherwise
    returns ``None`` for subdomain routing.
    """
    # Strip protocol if present
    url = url.strip()
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break

    # Strip trailing slash
    url = url.rstrip("/")

    if "/" in url:
        domain, path = url.split("/", 1)
        return domain, path
    return url, None

# %% [markdown]
# ## generate_caddy_config
#
# Renders the correct Jinja2 template based on routing type and deployment method.

# %%
#|export
def generate_caddy_config(
    domain: str,
    port: int | None = None,
    path: str | None = None,
    method: str = "command",
    source_path: str | None = None,
    apps: list[dict] | None = None,
) -> str:
    """Render a Caddy config snippet for an app.

    For subdirectory routing with multiple apps on the same domain,
    pass *apps* (list of dicts with keys ``path``, ``port``, ``method``,
    ``source_path``) to render a merged config.
    """
    # Subdirectory: merged config for one or more apps on the same domain
    if apps is not None:
        tmpl = _jinja_env.get_template("Caddyfile.subdirectory.j2")
        return tmpl.render(domain=domain, apps=apps)

    # Subdomain: static site
    if method == "static" and path is None:
        tmpl = _jinja_env.get_template("Caddyfile.static.j2")
        return tmpl.render(domain=domain, source_path=source_path)

    # Subdomain: reverse-proxy (command, dockerfile, docker-compose, auto)
    if path is None:
        tmpl = _jinja_env.get_template("Caddyfile.subdomain.j2")
        return tmpl.render(domain=domain, port=port)

    # Single subdirectory app — wrap in a list and render merged template
    tmpl = _jinja_env.get_template("Caddyfile.subdirectory.j2")
    return tmpl.render(domain=domain, apps=[{
        "path": path,
        "port": port,
        "method": method,
        "source_path": source_path,
    }])

# %% [markdown]
# ## deploy_caddy_config
#
# Writes a `.caddy` file to the remote server and reloads Caddy.
# For subdirectory apps sharing a domain, all apps on that domain
# are merged into one file.

# %%
#|export
def _caddy_file_path(app_name: str) -> str:
    """Return the remote path for an app's Caddy config."""
    return f"{CADDY_APPS_DIR}/{app_name}.caddy"

# %%
#|export
def _domain_caddy_file_path(domain: str) -> str:
    """Return the remote path for a domain's merged subdirectory Caddy config."""
    safe_domain = domain.replace(".", "_")
    return f"{CADDY_APPS_DIR}/_subdir_{safe_domain}.caddy"

# %%
#|export
def _collect_subdirectory_apps(garden_state: dict, domain: str) -> list[dict]:
    """Collect all subdirectory apps on a given domain from garden state."""
    apps = []
    for name, app in garden_state.get("apps", {}).items():
        url = app.get("url", "")
        app_domain, app_path = parse_url(url)
        if app_domain == domain and app_path is not None:
            apps.append({
                "name": name,
                "path": app_path,
                "port": app.get("port"),
                "method": app.get("method", "command"),
                "source_path": app.get("source_path"),
            })
    return apps

# %%
#|export
def deploy_caddy_config(
    host,
    app_name: str,
    domain: str,
    port: int | None = None,
    path: str | None = None,
    method: str = "command",
    source_path: str | None = None,
    garden_state: dict | None = None,
) -> None:
    """Write a Caddy config for an app and reload Caddy.

    For subdirectory apps, reads garden state to merge all apps
    on the same domain into one file.
    """
    if path is not None:
        # Subdirectory routing: merge all apps on this domain
        if garden_state is None:
            garden_state = read_garden_state(host)
        apps = _collect_subdirectory_apps(garden_state, domain)

        # Include the current app (may not be in garden_state yet)
        if not any(a["name"] == app_name for a in apps):
            apps.append({
                "name": app_name,
                "path": path,
                "port": port,
                "method": method,
                "source_path": source_path,
            })

        config = generate_caddy_config(domain=domain, apps=apps)
        remote_path = _domain_caddy_file_path(domain)
    else:
        # Subdomain routing: one file per app
        config = generate_caddy_config(
            domain=domain, port=port, method=method, source_path=source_path,
        )
        remote_path = _caddy_file_path(app_name)

    write_remote_file(host, remote_path, config)
    run_remote_command(host, "systemctl reload caddy")

# %% [markdown]
# ## remove_caddy_config
#
# Removes an app's Caddy config. For subdirectory apps, regenerates
# the merged domain file without the removed app.

# %%
#|export
def remove_caddy_config(
    host,
    app_name: str,
    domain: str,
    path: str | None = None,
    garden_state: dict | None = None,
) -> None:
    """Remove an app's Caddy config and reload Caddy.

    For subdirectory apps, regenerates the merged domain config
    without the removed app.  Deletes the file entirely if no
    apps remain on that domain.
    """
    if path is not None:
        # Subdirectory: regenerate merged config without this app
        if garden_state is None:
            garden_state = read_garden_state(host)
        apps = [a for a in _collect_subdirectory_apps(garden_state, domain)
                if a["name"] != app_name]

        remote_path = _domain_caddy_file_path(domain)
        if apps:
            config = generate_caddy_config(domain=domain, apps=apps)
            write_remote_file(host, remote_path, config)
        else:
            run_remote_command(host, f"rm -f {remote_path}")
    else:
        # Subdomain: just remove the file
        remote_path = _caddy_file_path(app_name)
        run_remote_command(host, f"rm -f {remote_path}")

    run_remote_command(host, "systemctl reload caddy")

# %% [markdown]
# ## Template rendering helpers
#
# Convenience functions for rendering non-Caddy templates
# (used by later phases for deployment).

# %%
#|export
def render_template(template_name: str, **kwargs) -> str:
    """Render a Jinja2 template by name from the templates directory."""
    tmpl = _jinja_env.get_template(template_name)
    return tmpl.render(**kwargs)
