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
from appgarden.remote import read_ports_state, write_ports_state

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
    Raises ``ValueError`` if *app_name* already has a port.
    """
    # Check if app already has a port
    for port_str, name in ports["allocated"].items():
        if name == app_name:
            raise ValueError(f"App '{app_name}' already has port {port_str} allocated")

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

# %% [markdown]
# ## Remote-aware functions
#
# These read from / write to the remote server via the host connection.

# %%
#|export
def allocate_port(host, app_name: str) -> int:
    """Allocate a port on the remote server for *app_name*."""
    ports = read_ports_state(host)
    ports, port = _allocate_port(ports, app_name)
    write_ports_state(host, ports)
    return port

# %%
#|export
def release_port(host, app_name: str) -> None:
    """Release the port held by *app_name* on the remote server."""
    ports = read_ports_state(host)
    ports = _release_port(ports, app_name)
    write_ports_state(host, ports)

# %%
#|export
def register_port(host, port: int, app_name: str) -> None:
    """Register a user-specified *port* for *app_name* on the remote server."""
    ports = read_ports_state(host)
    ports = _register_port(ports, port, app_name)
    write_ports_state(host, ports)

# %%
#|export
def get_app_port(host, app_name: str) -> int | None:
    """Return the port allocated to *app_name*, or ``None`` if none."""
    ports = read_ports_state(host)
    for port_str, name in ports["allocated"].items():
        if name == app_name:
            return int(port_str)
    return None
