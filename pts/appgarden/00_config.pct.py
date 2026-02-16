# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp config

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Configuration
#
# Local configuration management for AppGarden. Stores server definitions
# and defaults in `~/.config/appgarden/config.toml`.

# %%
#|export
import os
import subprocess
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import tomli_w

# %% [markdown]
# ## Data classes

# %%
#|export
@dataclass
class InitConfig:
    """Per-server init step configuration."""
    skip: list[str] = field(default_factory=list)

@dataclass
class ServerConfig:
    """Configuration for a single server."""
    ssh_user: str
    ssh_key: str          # path, ~ expanded on use
    domain: str
    host: str | None = None
    hcloud_name: str | None = None
    hcloud_context: str | None = None
    app_root: str | None = None  # default None → "/srv/appgarden"
    init: InitConfig = field(default_factory=InitConfig)

# %%
#|export
@dataclass
class AppGardenConfig:
    """Top-level AppGarden configuration."""
    default_server: str | None = None
    servers: dict[str, ServerConfig] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)

# %% [markdown]
# ## Config file paths

# %%
#|export
def config_dir() -> Path:
    """Return the AppGarden config directory, creating it if needed."""
    d = Path.home() / ".config" / "appgarden"
    d.mkdir(parents=True, exist_ok=True)
    return d

# %%
#|export
def config_path() -> Path:
    """Return the path to the config file."""
    return config_dir() / "config.toml"

# %% [markdown]
# ## Load / Save

# %%
#|export
def load_config(path: Path | None = None) -> AppGardenConfig:
    """Load configuration from TOML. Returns empty config if file is missing."""
    p = path or config_path()
    if not p.exists():
        return AppGardenConfig()

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    valid_keys = {f.name for f in ServerConfig.__dataclass_fields__.values()}
    servers = {}
    for name, sdata in raw.get("servers", {}).items():
        sdata = dict(sdata)  # copy so we can pop
        # Parse nested [servers.X.init] sub-table
        init_data = sdata.pop("init", None)
        init_cfg = InitConfig(**init_data) if init_data else InitConfig()

        unknown = set(sdata) - valid_keys
        if unknown:
            raise ValueError(
                f"Unknown key(s) in [servers.{name}]: {', '.join(sorted(unknown))}. "
                f"Valid keys: {', '.join(sorted(valid_keys))}"
            )
        servers[name] = ServerConfig(**sdata, init=init_cfg)

    return AppGardenConfig(
        default_server=raw.get("default_server"),
        servers=servers,
        defaults=dict(raw.get("defaults", {})),
    )

# %%
#|export
def save_config(config: AppGardenConfig, path: Path | None = None) -> None:
    """Write configuration to TOML."""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {}
    if config.default_server is not None:
        raw["default_server"] = config.default_server

    if config.defaults:
        raw["defaults"] = dict(config.defaults)

    if config.servers:
        raw["servers"] = {}
        for name, srv in config.servers.items():
            d = asdict(srv)
            # Drop None values for cleaner TOML
            d = {k: v for k, v in d.items() if v is not None}
            # Only include init sub-table if it has content
            init_d = d.pop("init", {})
            if init_d and init_d.get("skip"):
                d["init"] = init_d
            raw["servers"][name] = d

    with open(p, "wb") as f:
        tomli_w.dump(raw, f)
    os.chmod(p, 0o600)

# %% [markdown]
# ## Host resolution

# %%
#|export
def resolve_host(server: ServerConfig) -> str:
    """Resolve the server's IP address.

    Returns ``host`` directly if set, otherwise calls ``hcloud`` to look up
    the IP from the server's ``hcloud_name`` and ``hcloud_context``.
    """
    if server.host:
        return server.host

    if not server.hcloud_name or not server.hcloud_context:
        raise ValueError("Server must have either 'host' or both 'hcloud_name' and 'hcloud_context'")

    try:
        result = subprocess.run(
            ["hcloud", "--context", server.hcloud_context, "server", "ip", server.hcloud_name],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        raise ValueError("'hcloud' CLI not found. Install it from https://github.com/hetznercloud/cli")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        raise ValueError(
            f"Failed to resolve IP for hcloud server '{server.hcloud_name}' "
            f"(context '{server.hcloud_context}'): {stderr or 'unknown error'}"
        )
    return result.stdout.strip()

# %% [markdown]
# ## Server lookup

# %%
#|export
def get_server(config: AppGardenConfig, name: str | None = None) -> tuple[str, ServerConfig]:
    """Look up a server by name, falling back to the default server.

    Returns a ``(name, ServerConfig)`` tuple.
    """
    if name is None:
        name = config.default_server
    if name is None:
        raise ValueError("No server specified and no default server configured")
    if name not in config.servers:
        raise ValueError(f"Server '{name}' not found in configuration")
    return name, config.servers[name]
