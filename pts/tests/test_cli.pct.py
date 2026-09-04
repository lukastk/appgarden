# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_cli

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # CLI Tests
#
# Unit tests for the Typer CLI layer: deploy dispatch validation,
# `server init` step-option semantics, and `tunnel open` precondition checks.

# %%
#|export
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from appgarden.cli import app, _dispatch_deploy
from appgarden.config import AppGardenConfig, ServerConfig, InitConfig
from appgarden.server import INIT_STEPS, INIT_STEPS_OFF

runner = CliRunner()

# %%
#|export
def _make_cfg(init_skip=None):
    srv = ServerConfig(
        ssh_user="root", ssh_key="~/.ssh/id_rsa",
        domain="apps.example.com", host="1.2.3.4",
        init=InitConfig(skip=list(init_skip or [])),
    )
    return AppGardenConfig(default_server="myserver", servers={"myserver": srv})

# %% [markdown]
# ## _dispatch_deploy: per-method required arguments

# %%
#|export
def test_dispatch_deploy_static_requires_source():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "static", "a.apps.example.com")

# %%
#|export
def test_dispatch_deploy_command_requires_cmd():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "command", "a.apps.example.com")

# %%
#|export
def test_dispatch_deploy_docker_compose_requires_source():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "docker-compose", "a.apps.example.com")

# %%
#|export
def test_dispatch_deploy_dockerfile_requires_source():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "dockerfile", "a.apps.example.com")

# %%
#|export
def test_dispatch_deploy_auto_requires_source_and_cmd():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "auto", "a.apps.example.com", cmd="npm start")
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "auto", "a.apps.example.com", source="/tmp/src")

# %%
#|export
def test_dispatch_deploy_unknown_method():
    srv = _make_cfg().servers["myserver"]
    with pytest.raises(typer.Exit):
        _dispatch_deploy(srv, "a", "kubernetes", "a.apps.example.com")

# %%
#|export
def test_dispatch_deploy_routes_by_method():
    """Each method dispatches to its deploy function with the right args."""
    srv = _make_cfg().servers["myserver"]
    with patch("appgarden.cli.deploy_static") as m_static, \
         patch("appgarden.cli.deploy_command") as m_command, \
         patch("appgarden.cli.deploy_auto") as m_auto:
        _dispatch_deploy(srv, "a", "static", "a.apps.example.com", source="/tmp/s")
        m_static.assert_called_once()
        _dispatch_deploy(srv, "a", "command", "a.apps.example.com", cmd="python app.py")
        m_command.assert_called_once()
        _dispatch_deploy(srv, "a", "auto", "a.apps.example.com",
                         source="/tmp/s", cmd="npm start")
        m_auto.assert_called_once()

# %% [markdown]
# ## server init: --skip / --include / --minimal semantics

# %%
#|export
def _run_init(cfg, *args):
    with patch("appgarden.cli.load_config", return_value=cfg), \
         patch("appgarden.cli.init_server") as mock_init:
        result = runner.invoke(app, ["server", "init", "myserver", *args])
    return result, mock_init

# %%
#|export
def test_server_init_default_skips_opt_in_steps():
    """A plain init skips exactly the off-by-default steps."""
    result, mock_init = _run_init(_make_cfg())
    assert result.exit_code == 0, result.output
    assert mock_init.call_args.kwargs["skip"] == set(INIT_STEPS_OFF)

# %%
#|export
def test_server_init_minimal_skips_everything():
    result, mock_init = _run_init(_make_cfg(), "--minimal")
    assert result.exit_code == 0, result.output
    assert mock_init.call_args.kwargs["skip"] == set(INIT_STEPS)

# %%
#|export
def test_server_init_include_wins_even_with_minimal():
    """--include re-enables a step even under --minimal."""
    result, mock_init = _run_init(_make_cfg(), "--minimal", "--include", "group")
    assert result.exit_code == 0, result.output
    assert mock_init.call_args.kwargs["skip"] == set(INIT_STEPS) - {"group"}

# %%
#|export
def test_server_init_include_enables_opt_in_step():
    result, mock_init = _run_init(_make_cfg(), "--include", "firewall")
    assert result.exit_code == 0, result.output
    assert mock_init.call_args.kwargs["skip"] == set(INIT_STEPS_OFF) - {"firewall"}

# %%
#|export
def test_server_init_config_skip_merged():
    """Persistent [servers.X.init] skip entries merge with the defaults."""
    result, mock_init = _run_init(_make_cfg(init_skip=["docker"]))
    assert result.exit_code == 0, result.output
    assert mock_init.call_args.kwargs["skip"] == set(INIT_STEPS_OFF) | {"docker"}

# %%
#|export
def test_server_init_unknown_step_rejected():
    result, mock_init = _run_init(_make_cfg(), "--skip", "frobnicate")
    assert result.exit_code == 1
    assert "Unknown init step" in result.output
    mock_init.assert_not_called()

# %% [markdown]
# ## tunnel open: precondition checks

# %%
#|export
def _run_tunnel(*args):
    with patch("appgarden.cli.load_config", return_value=_make_cfg()), \
         patch("appgarden.cli.open_tunnel") as mock_open:
        result = runner.invoke(app, ["tunnel", "open", *args])
    return result, mock_open

# %%
#|export
def test_tunnel_open_cmd_and_serve_exclusive(tmp_path):
    result, mock_open = _run_tunnel("3000", "--cmd", "x", "--serve", str(tmp_path))
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_include_requires_serve():
    result, mock_open = _run_tunnel("3000", "--include", "*.md")
    assert result.exit_code == 1
    assert "--serve" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_url_and_subdomain_exclusive():
    result, mock_open = _run_tunnel("3000", "--url", "a.example.com", "--subdomain", "x")
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_serve_path_must_exist():
    result, mock_open = _run_tunnel("3000", "--serve", "/nonexistent/appgarden-test-xyz")
    assert result.exit_code == 1
    assert "does not exist" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_include_requires_serve_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    result, mock_open = _run_tunnel("3000", "--serve", str(f), "--include", "*.md")
    assert result.exit_code == 1
    assert "directory" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_port_required_unless_serve():
    result, mock_open = _run_tunnel()
    assert result.exit_code == 1
    assert "LOCAL_PORT is required" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_serve_picks_free_port(tmp_path):
    """With --serve and no LOCAL_PORT, a free local port is picked automatically."""
    result, mock_open = _run_tunnel("--serve", str(tmp_path))
    assert result.exit_code == 0, result.output
    mock_open.assert_called_once()
    picked = mock_open.call_args.args[1]
    assert isinstance(picked, int) and picked > 0

# %%
#|export
def test_tunnel_open_subdomain_builds_url(tmp_path):
    """--subdomain foo becomes foo.<server-domain>."""
    result, mock_open = _run_tunnel("3000", "--subdomain", "foo")
    assert result.exit_code == 0, result.output
    url = mock_open.call_args.args[2]
    assert url == "foo.apps.example.com"

# %%
#|export
def test_tunnel_open_replace_requires_url_or_subdomain():
    """--replace with a generated subdomain could never match anything, so it
    is refused rather than silently replacing nothing."""
    result, mock_open = _run_tunnel("3000", "--replace")
    assert result.exit_code == 1
    assert "--replace requires" in result.output
    mock_open.assert_not_called()

# %%
#|export
def test_tunnel_open_replace_passed_through():
    """--replace reaches open_tunnel; without it the default stays False."""
    result, mock_open = _run_tunnel("3000", "--url", "t.example.com", "--replace")
    assert result.exit_code == 0, result.output
    assert mock_open.call_args.kwargs["replace"] is True

    result, mock_open = _run_tunnel("3000", "--url", "t.example.com")
    assert result.exit_code == 0, result.output
    assert mock_open.call_args.kwargs["replace"] is False

# %% [markdown]
# ## tunnel list --json

# %%
#|export
def test_tunnel_list_json_emits_parsable_array():
    """--json is the scripting surface: parsable output, no rich decoration."""
    import json as _json
    from appgarden.tunnel import TunnelInfo

    tunnels = [TunnelInfo(tunnel_id="tunnel-abc", url="t.example.com",
                          local_port=3000, remote_port=10000, created_at="2026-01-01")]
    with patch("appgarden.cli.load_config", return_value=_make_cfg()), \
         patch("appgarden.cli.ssh_connect"), \
         patch("appgarden.cli.list_tunnels", return_value=tunnels):
        result = runner.invoke(app, ["tunnel", "list", "--json"])

    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.output)
    assert parsed == [{"tunnel_id": "tunnel-abc", "url": "t.example.com",
                       "local_port": 3000, "remote_port": 10000, "created_at": "2026-01-01"}]

# %%
#|export
def test_tunnel_list_json_empty_is_empty_array():
    """No tunnels is an empty array, not the human 'No active tunnels.' line —
    a caller parsing the output must not have to special-case it."""
    import json as _json

    with patch("appgarden.cli.load_config", return_value=_make_cfg()), \
         patch("appgarden.cli.ssh_connect"), \
         patch("appgarden.cli.list_tunnels", return_value=[]):
        result = runner.invoke(app, ["tunnel", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert _json.loads(result.output) == []
