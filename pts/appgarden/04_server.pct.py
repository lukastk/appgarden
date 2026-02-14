# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp server

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Server Management
#
# Initialise a fresh server for AppGarden use and test connectivity.

# %%
#|export
import json

from rich.console import Console

from appgarden.config import ServerConfig, resolve_host
from appgarden.ports import empty_ports_state
from appgarden.remote import (
    APPGARDEN_ROOT, GARDEN_STATE_PATH, PORTS_PATH,
    ssh_connect, run_remote_command, write_remote_file,
)

console = Console()

# %% [markdown]
# ## ping_server

# %%
#|export
def ping_server(server: ServerConfig) -> bool:
    """Test SSH connectivity to a server. Returns True if reachable."""
    try:
        with ssh_connect(server) as host:
            run_remote_command(host, "echo ok")
        return True
    except Exception:
        return False

# %% [markdown]
# ## init_server
#
# Prepares a fresh Ubuntu/Debian server:
#
# 1. System updates
# 2. Docker CE + compose plugin
# 3. Caddy web server
# 4. UFW firewall
# 5. SSH hardening
# 6. fail2ban
# 7. unattended-upgrades
# 8. AppGarden directory structure + state files

# %%
#|export
CADDYFILE_CONTENT = """\
import /srv/appgarden/caddy/apps/*.caddy
import /srv/appgarden/caddy/tunnels/*.caddy
"""

SSH_HARDENING_CONTENT = """\
PasswordAuthentication no
MaxAuthTries 3
X11Forwarding no
"""

# %%
#|export
def _run(host, cmd: str, label: str, timeout: int = 300) -> None:
    """Run a command on the remote, printing a status label."""
    console.print(f"  [dim]{label}[/dim]")
    run_remote_command(host, cmd, timeout=timeout)

# %%
#|export
def init_server(server: ServerConfig) -> None:
    """Initialise a remote server for AppGarden use."""
    console.print(f"[bold]Initialising server[/bold] ({resolve_host(server)})")

    with ssh_connect(server) as host:
        # 1. System updates
        _run(host, "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq",
             "Updating system packages")

        # 2. Install Docker CE
        _run(host,
             "apt-get install -y -qq ca-certificates curl gnupg && "
             "install -m 0755 -d /etc/apt/keyrings && "
             ". /etc/os-release && "
             "curl -fsSL https://download.docker.com/linux/$ID/gpg -o /etc/apt/keyrings/docker.asc && "
             "chmod a+r /etc/apt/keyrings/docker.asc && "
             'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] '
             'https://download.docker.com/linux/$ID $VERSION_CODENAME stable" '
             "> /etc/apt/sources.list.d/docker.list && "
             "apt-get update -qq && "
             "apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin",
             "Installing Docker CE")

        # 3. Install Caddy
        _run(host,
             "apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl && "
             "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | "
             "gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
             "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | "
             "tee /etc/apt/sources.list.d/caddy-stable.list && "
             "apt-get update -qq && "
             "apt-get install -y -qq caddy",
             "Installing Caddy")

        # 4. Configure Caddy root Caddyfile
        write_remote_file(host, "/etc/caddy/Caddyfile", CADDYFILE_CONTENT)
        console.print("  [dim]Configured Caddyfile[/dim]")

        # 5. UFW firewall
        _run(host,
             "apt-get install -y -qq ufw && "
             "ufw default deny incoming && "
             "ufw default allow outgoing && "
             "ufw allow ssh && "
             "ufw allow http && "
             "ufw allow https && "
             "ufw --force enable",
             "Configuring firewall (UFW)")

        # 6. SSH hardening
        write_remote_file(host, "/etc/ssh/sshd_config.d/hardening.conf", SSH_HARDENING_CONTENT)
        _run(host, "systemctl reload sshd", "Hardening SSH")

        # 7. fail2ban
        _run(host, "apt-get install -y -qq fail2ban && systemctl enable fail2ban && systemctl start fail2ban",
             "Installing fail2ban")

        # 8. unattended-upgrades
        _run(host, "apt-get install -y -qq unattended-upgrades && "
             "dpkg-reconfigure -f noninteractive unattended-upgrades",
             "Enabling unattended-upgrades")

        # 9. AppGarden directory structure
        dirs = [
            f"{APPGARDEN_ROOT}/apps",
            f"{APPGARDEN_ROOT}/caddy/apps",
            f"{APPGARDEN_ROOT}/caddy/tunnels",
            f"{APPGARDEN_ROOT}/tunnels",
        ]
        _run(host, f"mkdir -p {' '.join(dirs)}", "Creating directory structure")

        # 10. Initialise state files
        write_remote_file(host, GARDEN_STATE_PATH, json.dumps({"apps": {}}, indent=2))
        write_remote_file(host, PORTS_PATH, json.dumps(empty_ports_state(), indent=2))
        console.print("  [dim]Initialised state files[/dim]")

        # Enable and start Docker + Caddy
        _run(host, "systemctl enable docker && systemctl start docker", "Starting Docker")
        _run(host, "systemctl enable caddy && systemctl restart caddy", "Starting Caddy")

    console.print("[bold green]Server initialised successfully.[/bold green]")
