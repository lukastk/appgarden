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

from appgarden.ports import (
    PORT_RANGE_START,
    empty_ports_state,
    _allocate_port,
    _release_port,
    _register_port,
)

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
