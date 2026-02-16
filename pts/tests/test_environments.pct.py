# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_environments

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Environment Tests
#
# Unit tests for appgarden.toml parsing, environment resolution,
# and app name derivation.

# %%
#|export
import pytest
from pathlib import Path

from appgarden.environments import (
    load_project_config, resolve_environment, resolve_all_environments,
    derive_app_name, list_environments,
    ProjectConfig, EnvironmentConfig,
)
from appgarden.cli import _resolve_deploy_params, _env_config_to_dict, DEPLOY_DEFAULTS

# %% [markdown]
# ## Sample TOML content

# %%
#|export
SAMPLE_TOML = """\
[app]
name = "mywebsite"
method = "dockerfile"
container_port = 3000
source = "."

[environments.production]
server = "myserver"
url = "mywebsite.apps.example.com"
branch = "main"
env = { NODE_ENV = "production" }

[environments.staging]
server = "myserver"
url = "mywebsite-staging.apps.example.com"
branch = "staging"
env = { NODE_ENV = "staging" }

[environments.dev]
server = "myserver"
url = "mywebsite-dev.apps.example.com"
branch = "develop"
env = { NODE_ENV = "development", DEBUG = "true" }
"""

# %% [markdown]
# ## load_project_config

# %%
#|export
def test_load_project_config(tmp_path):
    """Loads appgarden.toml and parses app + environments."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    assert cfg.app_name == "mywebsite"
    assert cfg.app_defaults["method"] == "dockerfile"
    assert "production" in cfg.environments
    assert "staging" in cfg.environments
    assert "dev" in cfg.environments

# %%
#|export
def test_load_project_config_missing(tmp_path):
    """Raises FileNotFoundError when no appgarden.toml exists."""
    with pytest.raises(FileNotFoundError):
        load_project_config(tmp_path)

# %%
#|export
def test_load_project_config_no_name(tmp_path):
    """Raises ValueError when [app] section has no name."""
    (tmp_path / "appgarden.toml").write_text("[app]\nmethod = 'static'\n")
    with pytest.raises(ValueError, match="must have.*name"):
        load_project_config(tmp_path)

# %% [markdown]
# ## derive_app_name

# %%
#|export
def test_derive_app_name_production():
    """Production environment uses base name without suffix."""
    assert derive_app_name("myapp", "production") == "myapp"

# %%
#|export
def test_derive_app_name_staging():
    """Non-production environments get a suffix."""
    assert derive_app_name("myapp", "staging") == "myapp-staging"

# %%
#|export
def test_derive_app_name_dev():
    """Dev environment gets -dev suffix."""
    assert derive_app_name("myapp", "dev") == "myapp-dev"

# %% [markdown]
# ## resolve_environment

# %%
#|export
def test_resolve_production(tmp_path):
    """Production env merges app defaults with env overrides."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")

    assert env.name == "production"
    assert env.app_name == "mywebsite"
    assert env.method == "dockerfile"
    assert env.url == "mywebsite.apps.example.com"
    assert env.branch == "main"
    assert env.container_port == 3000
    assert env.source == "."
    assert env.server == "myserver"
    assert env.env == {"NODE_ENV": "production"}

# %%
#|export
def test_resolve_staging(tmp_path):
    """Staging env has correct suffix and branch."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "staging")

    assert env.name == "staging"
    assert env.app_name == "mywebsite-staging"
    assert env.url == "mywebsite-staging.apps.example.com"
    assert env.branch == "staging"
    assert env.env == {"NODE_ENV": "staging"}

# %%
#|export
def test_resolve_env_merges_env_vars(tmp_path):
    """Environment env vars are merged with app-level env vars."""
    toml = """\
[app]
name = "myapp"
method = "command"
env = { SHARED = "base", OVERRIDE = "app" }

[environments.staging]
url = "staging.example.com"
env = { OVERRIDE = "staging", EXTRA = "yes" }
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "staging")

    assert env.env["SHARED"] == "base"
    assert env.env["OVERRIDE"] == "staging"
    assert env.env["EXTRA"] == "yes"

# %%
#|export
def test_resolve_unknown_env(tmp_path):
    """Raises ValueError for undefined environment."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        resolve_environment(cfg, "nonexistent")

# %% [markdown]
# ## list_environments & resolve_all

# %%
#|export
def test_list_environments(tmp_path):
    """list_environments returns sorted names."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    names = list_environments(cfg)
    assert names == ["dev", "production", "staging"]

# %%
#|export
def test_resolve_all_environments(tmp_path):
    """resolve_all_environments returns configs for all envs."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(tmp_path)
    envs = resolve_all_environments(cfg)
    assert len(envs) == 3
    names = {e.app_name for e in envs}
    assert names == {"mywebsite", "mywebsite-staging", "mywebsite-dev"}

# %% [markdown]
# ## Cascading config resolution

# %%
#|export
def test_cascade_cli_overrides_env():
    """CLI flags override environment config values."""
    env_cfg = {"method": "dockerfile", "branch": "main", "url": "app.example.com"}
    cli = {"branch": "hotfix", "method": None, "url": None}
    result = _resolve_deploy_params(cli, env_cfg=env_cfg)
    assert result["branch"] == "hotfix"
    assert result["method"] == "dockerfile"
    assert result["url"] == "app.example.com"

# %%
#|export
def test_cascade_global_defaults():
    """Global defaults fill in values not set by other layers."""
    cli = {"method": None, "source": None}
    global_defaults = {"method": "dockerfile", "container_port": 8080}
    result = _resolve_deploy_params(cli, global_defaults=global_defaults)
    assert result["method"] == "dockerfile"
    assert result["container_port"] == 8080

# %%
#|export
def test_cascade_full():
    """Full cascade: hardcoded < global < project < env < CLI."""
    global_defaults = {"method": "command", "container_port": 9090}
    project_defaults = {"method": "dockerfile", "source": "."}
    env_cfg = {"url": "app.example.com", "branch": "main"}
    cli = {"branch": "hotfix", "method": None, "source": None, "url": None}
    result = _resolve_deploy_params(cli, env_cfg=env_cfg, project_defaults=project_defaults, global_defaults=global_defaults)
    # CLI overrides branch
    assert result["branch"] == "hotfix"
    # env provides url
    assert result["url"] == "app.example.com"
    # project overrides global for method
    assert result["method"] == "dockerfile"
    # project provides source
    assert result["source"] == "."
    # global overrides hardcoded for container_port
    assert result["container_port"] == 9090

# %% [markdown]
# ## Subdomain / path / domain fields

# %%
#|export
def test_resolve_subdomain(tmp_path):
    """Environment with subdomain is preserved through resolution."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"

[environments.production]
subdomain = "myapp"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.subdomain == "myapp"
    assert env.url is None

# %%
#|export
def test_resolve_path_prefix(tmp_path):
    """Environment with path is preserved through resolution."""
    toml = """\
[app]
name = "myapp"
method = "static"

[environments.production]
path = "api"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.path == "api"
    assert env.subdomain is None

# %%
#|export
def test_cascade_subdomain_overrides_app_default(tmp_path):
    """Subdomain at [app] level can be overridden per environment."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
subdomain = "default-sub"

[environments.production]
subdomain = "prod-sub"

[environments.staging]
server = "myserver"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)

    prod = resolve_environment(cfg, "production")
    assert prod.subdomain == "prod-sub"

    staging = resolve_environment(cfg, "staging")
    assert staging.subdomain == "default-sub"

# %%
#|export
def test_cascade_subdomain_to_deploy_params(tmp_path):
    """Subdomain from env config flows through _env_config_to_dict into deploy params."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
source = "."

[environments.production]
subdomain = "myapp"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    env_dict = _env_config_to_dict(env)
    assert env_dict["subdomain"] == "myapp"

    cli = {"method": None, "source": None, "url": None, "subdomain": None}
    params = _resolve_deploy_params(cli, env_cfg=env_dict)
    assert params["subdomain"] == "myapp"
    assert params["method"] == "dockerfile"
    assert params["source"] == "."

# %% [markdown]
# ## load_project_config with explicit path

# %%
#|export
def test_load_project_config_explicit_dir(tmp_path):
    """load_project_config works when given an explicit directory path."""
    subdir = tmp_path / "myproject"
    subdir.mkdir()
    (subdir / "appgarden.toml").write_text(SAMPLE_TOML)
    cfg = load_project_config(subdir)
    assert cfg.app_name == "mywebsite"
    assert "production" in cfg.environments

# %%
#|export
def test_load_project_config_explicit_file_path(tmp_path):
    """load_project_config works when given a path to the toml file itself (parent dir used)."""
    (tmp_path / "appgarden.toml").write_text(SAMPLE_TOML)
    # Passing the file path directly should work via parent resolution in CLI,
    # but load_project_config expects a directory — verify that directly.
    from pathlib import Path
    file_path = tmp_path / "appgarden.toml"
    cfg = load_project_config(file_path.parent)
    assert cfg.app_name == "mywebsite"
    assert len(cfg.environments) == 3

# %% [markdown]
# ## Placeholder interpolation

# %%
#|export
def test_placeholder_app_name_in_subdomain(tmp_path):
    """'{app.name}' in subdomain is replaced with the app name."""
    toml = """\
[app]
name = "test-app"
method = "dockerfile"

[environments.production]
subdomain = "{app.name}"

[environments.dev]
subdomain = "{app.name}-dev"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)

    prod = resolve_environment(cfg, "production")
    assert prod.subdomain == "test-app"

    dev = resolve_environment(cfg, "dev")
    assert dev.subdomain == "test-app-dev"

# %%
#|export
def test_placeholder_env_name(tmp_path):
    """'{env.name}' is replaced with the environment name."""
    toml = """\
[app]
name = "myapp"
method = "command"

[environments.staging]
url = "myapp-{env.name}.example.com"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "staging")
    assert env.url == "myapp-staging.example.com"

# %%
#|export
def test_placeholder_in_env_vars(tmp_path):
    """Placeholders work in environment variable values."""
    toml = """\
[app]
name = "myapp"
method = "command"

[environments.production]
url = "myapp.example.com"
env = { APP_NAME = "{app.name}", DEPLOY_ENV = "{env.name}" }
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.env["APP_NAME"] == "myapp"
    assert env.env["DEPLOY_ENV"] == "production"

# %%
#|export
def test_placeholder_in_app_defaults(tmp_path):
    """Placeholders in [app]-level defaults are interpolated per environment."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
subdomain = "{app.name}-{env.name}"

[environments.production]
server = "myserver"

[environments.staging]
server = "myserver"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)

    prod = resolve_environment(cfg, "production")
    assert prod.subdomain == "myapp-production"

    staging = resolve_environment(cfg, "staging")
    assert staging.subdomain == "myapp-staging"

# %%
#|export
def test_no_placeholder_passthrough(tmp_path):
    """Strings without placeholders are left unchanged."""
    toml = """\
[app]
name = "myapp"
method = "static"

[environments.production]
subdomain = "literal-value"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.subdomain == "literal-value"

# %%
#|export
def test_placeholder_app_slug(tmp_path):
    """'{app.slug}' is replaced with the slug value."""
    toml = """\
[app]
name = "My Cool App"
slug = "my-cool-app"
method = "dockerfile"

[environments.production]
subdomain = "{app.slug}"

[environments.dev]
subdomain = "{app.slug}-dev"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    assert cfg.app_slug == "my-cool-app"

    prod = resolve_environment(cfg, "production")
    assert prod.subdomain == "my-cool-app"

    dev = resolve_environment(cfg, "dev")
    assert dev.subdomain == "my-cool-app-dev"

# %%
#|export
def test_placeholder_app_slug_fallback(tmp_path):
    """'{app.slug}' falls back to app name when slug is not set."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"

[environments.production]
subdomain = "{app.slug}"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    assert cfg.app_slug is None

    env = resolve_environment(cfg, "production")
    assert env.subdomain == "myapp"

# %%
#|export
def test_slug_not_in_app_defaults(tmp_path):
    """slug is not passed through as an app default."""
    toml = """\
[app]
name = "myapp"
slug = "my-app"
method = "dockerfile"

[environments.production]
subdomain = "prod"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    assert "slug" not in cfg.app_defaults

# %% [markdown]
# ## Metadata merging

# %%
#|export
def test_resolve_meta_from_app_defaults(tmp_path):
    """App-level meta is inherited by environments."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
meta = { team = "backend", visibility = "internal" }

[environments.production]
url = "myapp.example.com"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.meta == {"team": "backend", "visibility": "internal"}

# %%
#|export
def test_resolve_meta_env_overrides_app(tmp_path):
    """Environment meta overrides app-level meta keys."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
meta = { team = "backend", visibility = "internal" }

[environments.production]
url = "myapp.example.com"
meta = { visibility = "public" }
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.meta == {"team": "backend", "visibility": "public"}

# %%
#|export
def test_resolve_meta_env_adds_keys(tmp_path):
    """Environment can add new meta keys beyond app defaults."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
meta = { team = "backend" }

[environments.production]
url = "myapp.example.com"
meta = { tier = "premium" }
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.meta == {"team": "backend", "tier": "premium"}

# %%
#|export
def test_resolve_meta_empty_default(tmp_path):
    """Environments without meta get empty dict."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"

[environments.production]
url = "myapp.example.com"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.meta == {}

# %%
#|export
def test_env_config_to_dict_includes_meta():
    """_env_config_to_dict includes non-empty meta."""
    env = EnvironmentConfig(
        name="production", app_name="myapp",
        method="dockerfile", url="myapp.example.com",
        meta={"team": "backend"},
    )
    d = _env_config_to_dict(env)
    assert d["meta"] == {"team": "backend"}

# %%
#|export
def test_env_config_to_dict_excludes_empty_meta():
    """_env_config_to_dict excludes empty meta."""
    env = EnvironmentConfig(
        name="production", app_name="myapp",
        method="dockerfile", url="myapp.example.com",
    )
    d = _env_config_to_dict(env)
    assert "meta" not in d

# %% [markdown]
# ## Exclude list merging

# %%
#|export
def test_resolve_exclude_from_app_defaults(tmp_path):
    """App-level exclude is inherited by environments."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
exclude = ["node_modules", ".git"]

[environments.production]
url = "myapp.example.com"
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.exclude == ["node_modules", ".git"]

# %%
#|export
def test_resolve_exclude_concatenation(tmp_path):
    """Environment exclude is concatenated with app-level exclude."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
exclude = ["node_modules"]

[environments.production]
url = "myapp.example.com"
exclude = [".env", "dist"]
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.exclude == ["node_modules", ".env", "dist"]

# %%
#|export
def test_resolve_exclude_dedup(tmp_path):
    """Duplicate exclude patterns are removed (preserving order)."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"
exclude = ["node_modules", ".env"]

[environments.production]
url = "myapp.example.com"
exclude = [".env", "dist"]
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.exclude == ["node_modules", ".env", "dist"]

# %%
#|export
def test_resolve_gitignore_override(tmp_path):
    """Environment can set gitignore = false."""
    toml = """\
[app]
name = "myapp"
method = "dockerfile"

[environments.production]
url = "myapp.example.com"
gitignore = false
"""
    (tmp_path / "appgarden.toml").write_text(toml)
    cfg = load_project_config(tmp_path)
    env = resolve_environment(cfg, "production")
    assert env.gitignore is False

# %%
#|export
def test_env_config_to_dict_includes_exclude():
    """_env_config_to_dict includes non-empty exclude and gitignore=False."""
    env = EnvironmentConfig(
        name="production", app_name="myapp",
        method="dockerfile", url="myapp.example.com",
        exclude=["node_modules", ".env"],
        gitignore=False,
    )
    d = _env_config_to_dict(env)
    assert d["exclude"] == ["node_modules", ".env"]
    assert d["gitignore"] is False

# %%
#|export
def test_env_config_to_dict_omits_default_gitignore():
    """_env_config_to_dict omits gitignore when it's True (default)."""
    env = EnvironmentConfig(
        name="production", app_name="myapp",
        method="dockerfile", url="myapp.example.com",
    )
    d = _env_config_to_dict(env)
    assert "gitignore" not in d
    assert "exclude" not in d
