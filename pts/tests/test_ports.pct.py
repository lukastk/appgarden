# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_ports

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Port Management Tests
#
# Unit tests for the pure port allocation logic.

# %%
#|export
import pytest

import appgarden.ports as ports_mod
from appgarden.ports import (
    PORT_RANGE_START,
    empty_ports_state,
    _allocate_port,
    _release_port,
    _register_port,
    allocate_port,
    release_port,
    get_app_port,
)
from appgarden.remote import RemoteContext

# %% [markdown]
# ## empty_ports_state

# %%
#|export
def test_empty_ports_state():
    """Fresh state starts at PORT_RANGE_START with no allocations."""
    ports = empty_ports_state()
    assert ports["next_port"] == PORT_RANGE_START
    assert ports["allocated"] == {}

# %% [markdown]
# ## _allocate_port

# %%
#|export
def test_allocate_first_port():
    """First allocation returns PORT_RANGE_START."""
    ports = empty_ports_state()
    ports, port = _allocate_port(ports, "myapp")
    assert port == PORT_RANGE_START
    assert ports["allocated"][str(PORT_RANGE_START)] == "myapp"
    assert ports["next_port"] == PORT_RANGE_START + 1

# %%
#|export
def test_allocate_increments():
    """Successive allocations produce incrementing ports."""
    ports = empty_ports_state()
    ports, p1 = _allocate_port(ports, "app1")
    ports, p2 = _allocate_port(ports, "app2")
    ports, p3 = _allocate_port(ports, "app3")
    assert p1 == PORT_RANGE_START
    assert p2 == PORT_RANGE_START + 1
    assert p3 == PORT_RANGE_START + 2

# %%
#|export
def test_allocate_duplicate_app_returns_existing():
    """Allocating twice for the same app returns the existing port."""
    ports = empty_ports_state()
    ports, p1 = _allocate_port(ports, "myapp")
    ports, p2 = _allocate_port(ports, "myapp")
    assert p1 == p2
    assert ports["next_port"] == PORT_RANGE_START + 1  # not incremented again

# %% [markdown]
# ## _release_port

# %%
#|export
def test_release_port():
    """Releasing a port removes it from allocated."""
    ports = empty_ports_state()
    ports, port = _allocate_port(ports, "myapp")
    ports = _release_port(ports, "myapp")
    assert str(port) not in ports["allocated"]

# %%
#|export
def test_release_nonexistent_raises():
    """Releasing a port for an unknown app raises ValueError."""
    ports = empty_ports_state()
    with pytest.raises(ValueError, match="No port allocated"):
        _release_port(ports, "ghost")

# %%
#|export
def test_allocate_after_release():
    """After release, new allocations still increment (no reuse)."""
    ports = empty_ports_state()
    ports, p1 = _allocate_port(ports, "app1")
    ports, p2 = _allocate_port(ports, "app2")
    ports = _release_port(ports, "app1")
    ports, p3 = _allocate_port(ports, "app3")
    assert p3 == PORT_RANGE_START + 2  # next_port continues from 2

# %% [markdown]
# ## _register_port

# %%
#|export
def test_register_port():
    """Register a specific port for an app."""
    ports = empty_ports_state()
    ports = _register_port(ports, 8080, "custom")
    assert ports["allocated"]["8080"] == "custom"

# %%
#|export
def test_register_port_conflict():
    """Registering an already-used port raises ValueError."""
    ports = empty_ports_state()
    ports = _register_port(ports, 8080, "first")
    with pytest.raises(ValueError, match="already allocated"):
        _register_port(ports, 8080, "second")

# %%
#|export
def test_register_port_advances_next():
    """Registering a port >= next_port advances next_port."""
    ports = empty_ports_state()
    ports = _register_port(ports, 10005, "app")
    assert ports["next_port"] == 10006

# %%
#|export
def test_register_port_below_next():
    """Registering a port below next_port doesn't change next_port."""
    ports = {"next_port": 10010, "allocated": {}}
    ports = _register_port(ports, 10005, "app")
    assert ports["next_port"] == 10010

# %% [markdown]
# ## Remote-aware functions respect ctx.app_root
#
# These verify that `allocate_port` / `release_port` / `get_app_port` thread
# the `ctx` (and therefore the target server's `app_root`) down to the state
# accessors, so two AppGarden instances on one host keep independent ports.json
# files instead of colliding on the default `/srv/appgarden/ports.json`.

# %%
#|export
def test_allocate_port_respects_app_root(monkeypatch):
    """allocate/get/release operate on per-app_root state, not a shared default."""
    # In-memory ports.json keyed by app_root, standing in for the remote files.
    stores: dict[str, dict] = {}

    def _key(ctx):
        return ctx.app_root if ctx is not None else "__default__"

    def fake_read_locked(host, ctx=None):
        return stores.setdefault(_key(ctx), empty_ports_state())

    def fake_write_locked(host, state, ctx=None):
        stores[_key(ctx)] = state

    monkeypatch.setattr(ports_mod, "read_ports_state_locked", fake_read_locked)
    monkeypatch.setattr(ports_mod, "write_ports_state_locked", fake_write_locked)
    monkeypatch.setattr(ports_mod, "read_ports_state", fake_read_locked)

    main_ctx = RemoteContext(app_root="/srv/appgarden")
    proto_ctx = RemoteContext(app_root="/srv/appgarden-proto")

    p_main = allocate_port(None, "app-a", ctx=main_ctx)
    p_proto = allocate_port(None, "app-b", ctx=proto_ctx)

    # Each app is recorded in its own instance's store, not the other's.
    assert stores["/srv/appgarden"]["allocated"] == {str(p_main): "app-a"}
    assert stores["/srv/appgarden-proto"]["allocated"] == {str(p_proto): "app-b"}

    # get_app_port reads from the correct instance.
    assert get_app_port(None, "app-a", ctx=main_ctx) == p_main
    assert get_app_port(None, "app-a", ctx=proto_ctx) is None

    # release_port only touches the targeted instance.
    release_port(None, "app-a", ctx=main_ctx)
    assert stores["/srv/appgarden"]["allocated"] == {}
    assert stores["/srv/appgarden-proto"]["allocated"] == {str(p_proto): "app-b"}
