# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp environments

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Environment Handling
#
# Parse `appgarden.toml` project config and resolve deployment
# configurations for named environments.

# %%
#|export
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

# %% [markdown]
# ## Data model

# %%
#|export
@dataclass
class EnvironmentConfig:
    """Resolved deployment configuration for a single environment."""
    name: str
    app_name: str
    server: str | None = None
    method: str | None = None
    url: str | None = None
    source: str | None = None
    port: int | None = None
    container_port: int | None = None
    cmd: str | None = None
    setup_cmd: str | None = None
    branch: str | None = None
    subdomain: str | None = None
    path: str | None = None
    domain: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    env_file: str | None = None
    meta: dict = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    gitignore: bool = True

# %%
#|export
@dataclass
class ProjectConfig:
    """Parsed appgarden.toml project configuration."""
    app_name: str
    app_slug: str | None = None
    app_defaults: dict = field(default_factory=dict)
    environments: dict[str, dict] = field(default_factory=dict)

# %% [markdown]
# ## load_project_config

# %%
#|export
def load_project_config(path: str | Path = ".") -> ProjectConfig:
    """Load project config from a directory or explicit toml file.

    If *path* points to a file, that file is loaded directly.
    If *path* is a directory, ``appgarden.toml`` inside it is loaded.
    Raises FileNotFoundError if the file does not exist.
    """
    p = Path(path)
    if p.is_file():
        pass  # use the file as-is
    else:
        p = p / "appgarden.toml"
    if not p.exists():
        raise FileNotFoundError(f"No appgarden.toml found in {Path(path).resolve()}")

    with open(p, "rb") as f:
        data = tomllib.load(f)

    app_section = data.get("app", {})
    app_name = app_section.get("name")
    if not app_name:
        raise ValueError("appgarden.toml must have [app] name")
    app_slug = app_section.get("slug")

    environments = {}
    for env_name, env_data in data.get("environments", {}).items():
        environments[env_name] = dict(env_data)

    return ProjectConfig(
        app_name=app_name,
        app_slug=app_slug,
        app_defaults={k: v for k, v in app_section.items() if k not in ("name", "slug")},
        environments=environments,
    )

# %% [markdown]
# ## derive_app_name

# %%
#|export
def derive_app_name(base_name: str, env_name: str) -> str:
    """Derive the deployed app name from base name and environment.

    Production environment uses the base name directly;
    other environments get a suffix.
    """
    if env_name == "production":
        return base_name
    return f"{base_name}-{env_name}"

# %% [markdown]
# ## resolve_environment

# %%
#|export
def resolve_environment(config: ProjectConfig, env_name: str) -> EnvironmentConfig:
    """Resolve a named environment into a full deployment configuration.

    Merges app-level defaults with environment-specific overrides.
    Raises ValueError if the environment is not defined.
    """
    if env_name not in config.environments:
        available = ", ".join(sorted(config.environments.keys())) or "(none)"
        raise ValueError(
            f"Environment '{env_name}' not found in appgarden.toml. "
            f"Available: {available}"
        )

    # Start with app defaults, then overlay environment config
    merged = dict(config.app_defaults)
    env_data = config.environments[env_name]

    # For env vars, merge dicts (env-level overrides app-level)
    app_env = merged.pop("env", {})
    env_env = env_data.get("env", {})
    merged_env = {**app_env, **env_env}

    # For meta, merge dicts (env-level overrides app-level)
    app_meta = merged.pop("meta", {})
    env_meta = env_data.get("meta", {})
    merged_meta = {**app_meta, **env_meta}

    # For exclude, concatenate and deduplicate (preserving order)
    app_exclude = merged.pop("exclude", [])
    env_exclude = env_data.get("exclude", [])
    seen: set[str] = set()
    merged_exclude: list[str] = []
    for pat in list(app_exclude) + list(env_exclude):
        if pat not in seen:
            seen.add(pat)
            merged_exclude.append(pat)

    # For volumes, concatenate and deduplicate (preserving order)
    app_volumes = merged.pop("volumes", [])
    env_volumes = env_data.get("volumes", [])
    seen_vol: set[str] = set()
    merged_volumes: list[str] = []
    for vol in list(app_volumes) + list(env_volumes):
        if vol not in seen_vol:
            seen_vol.add(vol)
            merged_volumes.append(vol)

    # Overlay all other env-specific keys
    for k, v in env_data.items():
        if k not in ("env", "meta", "exclude", "volumes"):
            merged[k] = v

    app_name = derive_app_name(config.app_name, env_name)

    # Interpolate placeholders in string values
    # {app.slug} falls back to {app.name} when slug is not set
    placeholders = {
        "app.name": config.app_name,
        "app.slug": config.app_slug or config.app_name,
        "env.name": env_name,
    }
    for k, v in merged.items():
        if isinstance(v, str):
            for ph, val in placeholders.items():
                v = v.replace("{" + ph + "}", val)
            merged[k] = v
    for k, v in merged_env.items():
        for ph, val in placeholders.items():
            v = v.replace("{" + ph + "}", val)
        merged_env[k] = v

    return EnvironmentConfig(
        name=env_name,
        app_name=app_name,
        server=merged.get("server"),
        method=merged.get("method"),
        url=merged.get("url"),
        source=merged.get("source"),
        port=merged.get("port"),
        container_port=merged.get("container_port"),
        cmd=merged.get("cmd"),
        setup_cmd=merged.get("setup_cmd"),
        branch=merged.get("branch"),
        subdomain=merged.get("subdomain"),
        path=merged.get("path"),
        domain=merged.get("domain"),
        env=merged_env,
        env_file=merged.get("env_file"),
        meta=merged_meta,
        exclude=merged_exclude,
        volumes=merged_volumes,
        gitignore=merged.get("gitignore", True),
    )

# %% [markdown]
# ## list_environments

# %%
#|export
def list_environments(config: ProjectConfig) -> list[str]:
    """Return sorted list of environment names."""
    return sorted(config.environments.keys())

# %% [markdown]
# ## resolve_all_environments

# %%
#|export
def resolve_all_environments(config: ProjectConfig) -> list[EnvironmentConfig]:
    """Resolve all environments into deployment configurations."""
    return [resolve_environment(config, name) for name in list_environments(config)]
