# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp ports

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Port Management
#
# Allocate, release, and register ports on a remote server.
# Port state is stored in `/srv/appgarden/ports.json`:
#
# ```json
# {
#     "next_port": 10003,
#     "allocated": {
#         "10000": "myapp",
#         "10001": "another-app"
#     }
# }
# ```

# %%
#|export
from appgarden.remote import (
    read_ports_state, write_ports_state,
    read_ports_state_locked, write_ports_state_locked,
)

# %% [markdown]
# ## Constants

# %%
#|export
PORT_RANGE_START = 10000

# %% [markdown]
# ## Pure data helpers
#
# These operate on the ports dict directly, making them easy to unit-test.

# %%
#|export
def empty_ports_state() -> dict:
    """Return a fresh, empty ports state."""
    return {"next_port": PORT_RANGE_START, "allocated": {}}

# %%
#|export
def _allocate_port(ports: dict, app_name: str) -> tuple[dict, int]:
    """Allocate the next available port for *app_name*.

    Returns ``(updated_ports, port_number)``.
    If *app_name* already has a port, returns the existing allocation.
    """
    # Return existing port if app already has one
    for port_str, name in ports["allocated"].items():
        if name == app_name:
            return ports, int(port_str)

    port = ports["next_port"]
    ports["allocated"][str(port)] = app_name
    ports["next_port"] = port + 1
    return ports, port

# %%
#|export
def _release_port(ports: dict, app_name: str) -> dict:
    """Release the port held by *app_name*.

    Returns the updated ports dict.
    Raises ``ValueError`` if *app_name* has no allocated port.
    """
    for port_str, name in list(ports["allocated"].items()):
        if name == app_name:
            del ports["allocated"][port_str]
            return ports
    raise ValueError(f"No port allocated for app '{app_name}'")

# %%
#|export
def _register_port(ports: dict, port: int, app_name: str) -> dict:
    """Register a specific *port* for *app_name* (e.g. user-specified port).

    Returns the updated ports dict.
    Raises ``ValueError`` if the port is already in use.
    """
    port_str = str(port)
    if port_str in ports["allocated"]:
        existing = ports["allocated"][port_str]
        raise ValueError(f"Port {port} already allocated to '{existing}'")
    ports["allocated"][port_str] = app_name
    # Advance next_port past this one if needed
    if port >= ports["next_port"]:
        ports["next_port"] = port + 1
    return ports

# %%
#|export
def merge_allocations(ports: dict, allocations: dict) -> dict:
    """Merge ``{port: app_name}`` into a ports state, advancing ``next_port``.

    Used to reconcile already-deployed app ports (e.g. from a garden's
    ``garden.json``) into the shared host-level registry. Idempotent: re-merging
    the same allocations is a no-op, so it is safe to run on every ``server init``.
    """
    next_port = ports.get("next_port", PORT_RANGE_START)
    allocated = dict(ports.get("allocated", {}))
    for port, name in allocations.items():
        allocated[str(port)] = name
        if int(port) >= next_port:
            next_port = int(port) + 1
    return {"next_port": next_port, "allocated": allocated}

# %% [markdown]
# ## Remote-aware functions
#
# These read from / write to the shared host-level ports registry via the host
# connection. Port allocations are box-global (every garden on a host shares the
# same TCP port space), so these take no ``ctx`` — there is one registry per host.

# %%
#|export
def allocate_port(host, app_name: str) -> int:
    """Allocate a port on the remote host for *app_name*."""
    ports = read_ports_state_locked(host)
    ports, port = _allocate_port(ports, app_name)
    write_ports_state_locked(host, ports)
    return port

# %%
#|export
def release_port(host, app_name: str) -> None:
    """Release the port held by *app_name* on the remote host."""
    ports = read_ports_state_locked(host)
    ports = _release_port(ports, app_name)
    write_ports_state_locked(host, ports)

# %%
#|export
def register_port(host, port: int, app_name: str) -> None:
    """Register a user-specified *port* for *app_name* on the remote host."""
    ports = read_ports_state_locked(host)
    ports = _register_port(ports, port, app_name)
    write_ports_state_locked(host, ports)

# %%
#|export
def get_app_port(host, app_name: str) -> int | None:
    """Return the port allocated to *app_name*, or ``None`` if none."""
    ports = read_ports_state(host)
    for port_str, name in ports["allocated"].items():
        if name == app_name:
            return int(port_str)
    return None
