# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_config

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Config Tests
#
# Unit tests for local configuration management.

# %%
#|export
from unittest.mock import patch, MagicMock

from appgarden.config import (
    AppGardenConfig, ServerConfig,
    load_config, save_config, resolve_host, get_server,
)

# %% [markdown]
# ## Load / Save round-trip

# %%
#|export
def test_load_missing_config(tmp_path):
    """Missing config file returns empty config."""
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.default_server is None
    assert cfg.servers == {}

# %%
#|export
def test_save_load_roundtrip(tmp_path):
    """Save then load produces the same config."""
    p = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        default_server="myserver",
        servers={
            "myserver": ServerConfig(
                ssh_user="root",
                ssh_key="~/.ssh/id_rsa",
                domain="apps.example.com",
                host="1.2.3.4",
            ),
        },
    )
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.default_server == "myserver"
    assert "myserver" in loaded.servers
    srv = loaded.servers["myserver"]
    assert srv.ssh_user == "root"
    assert srv.host == "1.2.3.4"
    assert srv.domain == "apps.example.com"

# %%
#|export
def test_save_strips_none_values(tmp_path):
    """None fields (host, hcloud_*) are omitted from the TOML file."""
    p = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        servers={
            "s1": ServerConfig(
                ssh_user="root", ssh_key="~/.ssh/id_rsa",
                domain="example.com", host=None,
                hcloud_name="myvm", hcloud_context="myctx",
            ),
        },
    )
    save_config(cfg, p)
    text = p.read_text()
    assert "host" not in text
    assert "hcloud_name" in text

# %% [markdown]
# ## Multiple servers

# %%
#|export
def test_multiple_servers_roundtrip(tmp_path):
    """Multiple servers survive a save/load cycle."""
    p = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        default_server="a",
        servers={
            "a": ServerConfig(ssh_user="root", ssh_key="k", domain="a.com", host="1.1.1.1"),
            "b": ServerConfig(ssh_user="deploy", ssh_key="k2", domain="b.com",
                              hcloud_name="vm", hcloud_context="ctx"),
        },
    )
    save_config(cfg, p)
    loaded = load_config(p)
    assert set(loaded.servers.keys()) == {"a", "b"}
    assert loaded.servers["b"].hcloud_name == "vm"

# %% [markdown]
# ## get_server

# %%
#|export
def test_get_server_by_name():
    """Look up a specific server by name."""
    cfg = AppGardenConfig(
        default_server="a",
        servers={
            "a": ServerConfig(ssh_user="root", ssh_key="k", domain="a.com", host="1.1.1.1"),
            "b": ServerConfig(ssh_user="root", ssh_key="k", domain="b.com", host="2.2.2.2"),
        },
    )
    name, srv = get_server(cfg, "b")
    assert name == "b"
    assert srv.host == "2.2.2.2"

# %%
#|export
def test_get_server_default():
    """Falls back to default_server when name is None."""
    cfg = AppGardenConfig(
        default_server="a",
        servers={
            "a": ServerConfig(ssh_user="root", ssh_key="k", domain="a.com", host="1.1.1.1"),
        },
    )
    name, srv = get_server(cfg)
    assert name == "a"

# %%
#|export
def test_get_server_no_default():
    """Raises when no name given and no default configured."""
    import pytest
    cfg = AppGardenConfig()
    with pytest.raises(ValueError, match="No server specified"):
        get_server(cfg)

# %%
#|export
def test_get_server_not_found():
    """Raises when the named server doesn't exist."""
    import pytest
    cfg = AppGardenConfig(servers={})
    with pytest.raises(ValueError, match="not found"):
        get_server(cfg, "missing")

# %% [markdown]
# ## resolve_host

# %%
#|export
def test_resolve_host_direct():
    """Returns host directly when it's set."""
    srv = ServerConfig(ssh_user="root", ssh_key="k", domain="d.com", host="5.5.5.5")
    assert resolve_host(srv) == "5.5.5.5"

# %%
#|export
def test_resolve_host_hcloud():
    """Calls hcloud CLI when host is not set."""
    srv = ServerConfig(
        ssh_user="root", ssh_key="k", domain="d.com",
        hcloud_name="myvm", hcloud_context="myctx",
    )
    mock_result = MagicMock()
    mock_result.stdout = "10.0.0.1\n"

    with patch("appgarden.config.subprocess.run", return_value=mock_result) as mock_run:
        ip = resolve_host(srv)

    assert ip == "10.0.0.1"
    mock_run.assert_called_once_with(
        ["hcloud", "--context", "myctx", "server", "ip", "myvm"],
        capture_output=True, text=True, check=True,
    )

# %%
#|export
def test_resolve_host_missing_hcloud_fields():
    """Raises when neither host nor hcloud fields are set."""
    import pytest
    srv = ServerConfig(ssh_user="root", ssh_key="k", domain="d.com")
    with pytest.raises(ValueError, match="hcloud_name"):
        resolve_host(srv)

# %% [markdown]
# ## CLI integration (via typer.testing)

# %%
#|export
from typer.testing import CliRunner
from appgarden.cli import app

runner = CliRunner()

# %%
#|export
def test_cli_server_add_with_host(tmp_path, monkeypatch):
    """server add with --host succeeds."""
    cfg_file = tmp_path / "config.toml"
    monkeypatch.setattr("appgarden.config.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.load_config", lambda: load_config(cfg_file))
    monkeypatch.setattr("appgarden.cli.save_config", lambda cfg: save_config(cfg, cfg_file))

    result = runner.invoke(app, [
        "server", "add", "myserver",
        "--host", "1.2.3.4",
        "--ssh-user", "root",
        "--ssh-key", "~/.ssh/id_rsa",
        "--domain", "apps.example.com",
    ])
    assert result.exit_code == 0
    assert "myserver" in result.output

    # Verify it was persisted
    cfg = load_config(cfg_file)
    assert "myserver" in cfg.servers
    assert cfg.default_server == "myserver"

# %%
#|export
def test_cli_server_add_requires_host_or_hcloud(tmp_path, monkeypatch):
    """server add fails without --host or --hcloud-name/--hcloud-context."""
    cfg_file = tmp_path / "config.toml"
    monkeypatch.setattr("appgarden.config.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.load_config", lambda: load_config(cfg_file))
    monkeypatch.setattr("appgarden.cli.save_config", lambda cfg: save_config(cfg, cfg_file))

    result = runner.invoke(app, [
        "server", "add", "bad",
        "--domain", "example.com",
    ])
    assert result.exit_code == 1

# %%
#|export
def test_cli_server_list_empty(tmp_path, monkeypatch):
    """server list with no servers shows message."""
    cfg_file = tmp_path / "config.toml"
    monkeypatch.setattr("appgarden.config.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.load_config", lambda: load_config(cfg_file))

    result = runner.invoke(app, ["server", "list"])
    assert result.exit_code == 0
    assert "No servers" in result.output

# %%
#|export
def test_cli_server_remove(tmp_path, monkeypatch):
    """server remove deletes a server."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        default_server="s1",
        servers={"s1": ServerConfig(ssh_user="root", ssh_key="k", domain="d.com", host="1.1.1.1")},
    )
    save_config(cfg, cfg_file)

    monkeypatch.setattr("appgarden.config.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.load_config", lambda: load_config(cfg_file))
    monkeypatch.setattr("appgarden.cli.save_config", lambda c: save_config(c, cfg_file))

    result = runner.invoke(app, ["server", "remove", "s1"])
    assert result.exit_code == 0
    loaded = load_config(cfg_file)
    assert "s1" not in loaded.servers

# %%
#|export
def test_cli_server_default(tmp_path, monkeypatch):
    """server default sets the default server."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        servers={
            "a": ServerConfig(ssh_user="root", ssh_key="k", domain="a.com", host="1.1.1.1"),
            "b": ServerConfig(ssh_user="root", ssh_key="k", domain="b.com", host="2.2.2.2"),
        },
    )
    save_config(cfg, cfg_file)

    monkeypatch.setattr("appgarden.config.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.config_path", lambda: cfg_file)
    monkeypatch.setattr("appgarden.cli.load_config", lambda: load_config(cfg_file))
    monkeypatch.setattr("appgarden.cli.save_config", lambda c: save_config(c, cfg_file))

    result = runner.invoke(app, ["server", "default", "b"])
    assert result.exit_code == 0
    loaded = load_config(cfg_file)
    assert loaded.default_server == "b"

# %%
#|export
def test_cli_config_show(tmp_path, monkeypatch):
    """config show prints the TOML file contents."""
    cfg_file = tmp_path / "config.toml"
    cfg = AppGardenConfig(
        default_server="s1",
        servers={"s1": ServerConfig(ssh_user="root", ssh_key="k", domain="d.com", host="1.1.1.1")},
    )
    save_config(cfg, cfg_file)

    monkeypatch.setattr("appgarden.cli.config_path", lambda: cfg_file)

    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "s1" in result.output
