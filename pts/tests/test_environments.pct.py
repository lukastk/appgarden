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
