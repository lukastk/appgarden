# AppGarden Project Specification

A Python CLI tool for quickly deploying web applications to remote servers in a secure, structured way.

## 1. Vision

AppGarden manages a "garden" of web applications on remote servers. It handles deployment, routing, TLS certificates, and lifecycle management — all without requiring a persistent daemon on the server. State is encoded in the filesystem and configuration files on the remote, and all operations are performed over SSH using `pyinfra`.

## 2. Architecture

### 2.1 Agentless Design

There is no AppGarden server process running on the remote. All operations are performed by running `pyinfra` operations from the local machine over SSH. The remote server's filesystem *is* the state:

```
/srv/appgarden/                    # Root directory
├── garden.json                    # Registry of all deployed apps
├── ports.json                     # Port allocation registry
├── apps/
│   ├── myapp/
│   │   ├── app.json               # App metadata & config
│   │   ├── docker-compose.yml     # (if docker-compose method)
│   │   ├── Dockerfile             # (if dockerfile/auto method)
│   │   ├── source/                # App source code (if applicable)
│   │   └── data/                  # Persistent data volumes
│   └── another-app/
│       └── ...
├── caddy/
│   ├── apps/                      # Per-app Caddy config snippets
│   │   ├── myapp.caddy
│   │   └── another-app.caddy
│   └── tunnels/                   # Temporary tunnel Caddy configs
│       └── tunnel-abc123.caddy
└── tunnels/
    └── active.json                # Currently active tunnels
```

### 2.2 Local Configuration

Stored at `~/.config/appgarden/config.toml`:

```toml
# Default server to use when --server is not specified
default_server = "myserver"

[servers.myserver]
host = "203.0.113.10"
ssh_user = "root"
ssh_key = "~/.ssh/hcloud"
domain = "apps.example.com"        # Base domain for this server

[servers.hetzner-box]
hcloud_name = "main"               # Server name in hcloud
hcloud_context = "my-project"      # hcloud CLI context
ssh_user = "root"
ssh_key = "~/.ssh/hcloud"
domain = "apps.lukastk.dev"
```

When `hcloud_name` and `hcloud_context` are provided instead of `host`, the IP address is resolved dynamically by running:

```bash
hcloud --context <context> server describe <name> -o json
```

and extracting the public IPv4 address, exactly as in the existing `my-servers` inventory pattern.

### 2.3 Project-Level Configuration (Optional)

An optional `appgarden.toml` can live in a project's root directory to define deployment presets:

```toml
[app]
name = "mywebsite"
method = "docker-compose"          # deployment method

[environments.production]
server = "myserver"
url = "mywebsite.apps.example.com"

[environments.staging]
server = "myserver"
url = "mywebsite-staging.apps.example.com"

[environments.dev]
server = "myserver"
url = "dev.apps.example.com/mywebsite"
```

This allows deploying with just `appgarden deploy production` instead of specifying all flags.

## 3. CLI Commands

### 3.1 Server Management

```bash
# Add a server with direct IP
appgarden server add <name> \
  --host <ip> \
  --ssh-user <user> \
  --ssh-key <path> \
  --domain <base-domain>

# Add a server using hcloud
appgarden server add <name> \
  --hcloud-name <name> \
  --hcloud-context <context> \
  --ssh-user <user> \
  --ssh-key <path> \
  --domain <base-domain>

# List configured servers
appgarden server list

# Remove a server from local config (does not affect remote)
appgarden server remove <name>

# Set the default server
appgarden server default <name>

# Initialise a server for AppGarden use (installs Docker, Caddy, etc.)
appgarden server init <name>
```

### 3.2 App Deployment

```bash
# Deploy an app (full flags)
appgarden deploy <app-name> \
  --server <server> \
  --method <method> \
  --url <url> \
  [--source <path-or-git-url>] \
  [--port <port>] \
  [--cmd <start-command>] \
  [--setup-cmd <setup-command>] \
  [--env KEY=VALUE ...] \
  [--env-file <path>]

# Deploy using a project's appgarden.toml environment
appgarden deploy <environment-name>

# Re-deploy an existing app (updates source, restarts)
appgarden redeploy <app-name> [--server <server>]
```

**Deployment methods** (`--method`):

| Method | Description | Required flags |
|--------|-------------|----------------|
| `docker-compose` | User provides a `docker-compose.yml` | `--source` (dir containing compose file) |
| `dockerfile` | User provides a `Dockerfile` | `--source`, `--port` |
| `auto` | Auto-generates a Dockerfile from repo | `--source`, `--port`, `--cmd` |
| `command` | Bare process managed by systemd | `--cmd`, `--port` |
| `static` | Static files served directly by Caddy | `--source` |

### 3.3 App Lifecycle

```bash
# List all apps on a server
appgarden list [--server <server>]

# Show detailed status of an app (running/stopped, URL, method, uptime, etc.)
appgarden status <app-name> [--server <server>]

# Stop / start / restart an app
appgarden stop <app-name> [--server <server>]
appgarden start <app-name> [--server <server>]
appgarden restart <app-name> [--server <server>]

# View app logs
appgarden logs <app-name> [--server <server>] [--follow] [--lines <n>]

# Remove an app entirely (stops service, removes files, removes Caddy config)
appgarden remove <app-name> [--server <server>] [--keep-data]
```

### 3.4 Localhost Tunneling

```bash
# Expose a local app through the remote server
appgarden tunnel <local-port> \
  --url <url> \
  --server <server>

# Example: expose localhost:3000 at myapp.apps.example.com
appgarden tunnel 3000 --url myapp.apps.example.com

# List active tunnels
appgarden tunnel list

# Close a tunnel
appgarden tunnel close <tunnel-id>
```

### 3.5 Info & Utilities

```bash
# Show appgarden version
appgarden version

# Show current config
appgarden config show

# Validate server connectivity
appgarden server ping <name>
```

## 4. Server Initialisation

`appgarden server init` prepares a fresh server. This follows the patterns from `my-servers/initialise_server.py`:

1. **System updates**: `apt update && apt upgrade`
2. **Install Docker**: Official Docker CE repository + `docker-compose-plugin`
3. **Install Caddy**: Official Caddy repository
4. **Configure Caddy root**:
   ```
   # /etc/caddy/Caddyfile
   import /srv/appgarden/caddy/apps/*.caddy
   import /srv/appgarden/caddy/tunnels/*.caddy
   ```
5. **Firewall (UFW)**: Allow SSH, HTTP (80), HTTPS (443). Default deny incoming.
6. **SSH hardening**: Disable password auth, limit retries, disable X11.
7. **Fail2ban**: Protect SSH.
8. **Automatic security updates**: `unattended-upgrades`.
9. **Create AppGarden directory structure**: `/srv/appgarden/{apps,caddy/apps,caddy/tunnels,tunnels}`, initialise `garden.json` and `ports.json`.

## 5. Deployment Methods in Detail

### 5.1 Docker Compose

The user provides a directory containing a `docker-compose.yml`. AppGarden:

1. Uploads the directory to `/srv/appgarden/apps/<name>/`
2. Uploads any `--env` values or `--env-file` as `/srv/appgarden/apps/<name>/.env`
3. Creates a systemd service unit:
   ```ini
   [Unit]
   Description=AppGarden: <app-name>
   Requires=docker.service
   After=docker.service

   [Service]
   WorkingDirectory=/srv/appgarden/apps/<app-name>
   ExecStart=/usr/bin/docker compose up
   ExecStop=/usr/bin/docker compose down
   Restart=on-failure
   StartLimitBurst=3

   [Install]
   WantedBy=multi-user.target
   ```
4. Creates the Caddy routing config (see Section 7)
5. Registers in `garden.json`
6. Starts the service

### 5.2 Dockerfile

The user provides a directory containing a `Dockerfile`. AppGarden:

1. Uploads the source to `/srv/appgarden/apps/<name>/source/`
2. Builds the image: `docker build -t appgarden-<name> .`
3. Generates a `docker-compose.yml`:
   ```yaml
   services:
     app:
       image: appgarden-<name>
       ports:
         - "<allocated-port>:<container-port>"
       restart: unless-stopped
       env_file:
         - .env
   ```
4. Proceeds as in Docker Compose method (systemd unit, Caddy config, etc.)

### 5.3 Auto-Docker

Given just a source directory (or git URL), a start command, and a port, AppGarden generates a Dockerfile. Detection heuristics:

| Indicator | Runtime | Base Image |
|-----------|---------|------------|
| `package.json` | Node.js | `node:22` |
| `requirements.txt` | Python (pip) | `python:3.12` |
| `pyproject.toml` | Python (uv/pip) | `python:3.12` |
| `Gemfile` | Ruby | `ruby:3.3` |
| `go.mod` | Go | `golang:1.23` |
| `Cargo.toml` | Rust | `rust:1.83` |

Generated Dockerfile pattern:

```dockerfile
FROM <base-image>
WORKDIR /app
COPY . .
RUN <setup-command>        # e.g., npm install, pip install -r requirements.txt
EXPOSE <port>
CMD <start-command>        # e.g., ["npm", "start"]
```

The `--setup-cmd` flag provides the build/install step. If not provided, AppGarden infers it from the detected runtime (e.g., `npm install` for Node.js, `pip install -r requirements.txt` for Python).

Then proceeds as in the Dockerfile method.

### 5.4 Command (Bare Process)

For apps that don't need Docker. AppGarden:

1. Uploads source to `/srv/appgarden/apps/<name>/source/` (if `--source` provided)
2. Creates a systemd service unit:
   ```ini
   [Unit]
   Description=AppGarden: <app-name>
   After=network.target

   [Service]
   WorkingDirectory=/srv/appgarden/apps/<app-name>/source
   ExecStart=<start-command>
   Restart=on-failure
   Environment=PORT=<allocated-port>

   [Install]
   WantedBy=multi-user.target
   ```
3. Creates Caddy routing config
4. Registers and starts

### 5.5 Static Site

For serving static files (HTML/CSS/JS). AppGarden:

1. Uploads files to `/srv/appgarden/apps/<name>/source/`
2. Creates a Caddy config that serves the directory directly (no reverse proxy):
   ```
   myapp.apps.example.com {
       root * /srv/appgarden/apps/<name>/source
       file_server
       try_files {path} /index.html   # SPA support
   }
   ```
3. No systemd service needed

## 6. Source Code Delivery

When `--source` is provided, it can be:

1. **Local directory path**: Uploaded via pyinfra's `files.rsync` or `files.put`
2. **Git URL**: Cloned on the server via `git clone`. A specific branch can be specified with `--branch`.

For `redeploy`, AppGarden:
- If the source was a git URL: runs `git pull` on the server
- If the source was a local directory: re-uploads via rsync
- Then rebuilds (if Docker) and restarts the service

## 7. URL Routing

### 7.1 Subdomain Routing (Recommended)

Apps are served at their own subdomain. Caddy config:

```
myapp.apps.example.com {
    reverse_proxy localhost:<port>
}
```

Caddy automatically obtains a TLS certificate for the subdomain via HTTP-01 challenge when it loads this config. Each app gets its own `.caddy` file with its explicit domain — no on-demand TLS needed.

### 7.2 Subdirectory Routing

Apps are served under a path on a shared domain. Caddy config:

```
apps.example.com {
    redir /myapp /myapp/
    handle_path /myapp/* {
        reverse_proxy localhost:<port> {
            header_up X-Forwarded-Prefix "/myapp"
        }
    }
}
```

`handle_path` strips the path prefix before forwarding to the app, so the app receives requests as if it's at `/`. The `X-Forwarded-Prefix` header tells frameworks that are aware of it where the app is actually mounted.

**Important limitations of subdirectory routing:**

- The app must support being mounted at a subpath. Many apps generate absolute URLs (e.g., `/css/style.css` instead of relative ones), which break when the app lives at `/myapp/`.
- Frameworks that support `X-Forwarded-Prefix` or a `BASE_URL`/`PUBLIC_PATH` environment variable will work correctly. These include: Express (with trust proxy), Django (`FORCE_SCRIPT_NAME`), Flask (`APPLICATION_ROOT`), Next.js (`basePath`), and others.
- Apps that hard-code absolute paths without base path support will have broken assets and links.
- **Recommendation**: Use subdomain routing when possible. Reserve subdirectory routing for apps that explicitly support base path configuration, or for static sites where asset paths can be controlled at build time.

### 7.3 Caddy Configuration Management

All per-app Caddy configs are stored as individual `.caddy` files under `/srv/appgarden/caddy/apps/`. The root Caddyfile imports them via glob. When apps share the same domain (subdirectory routing), their route blocks are combined into a single `.caddy` file for that domain.

After any Caddy config change, AppGarden runs `caddy reload` (preferred over restart for zero-downtime).

## 8. DNS Strategy

### 8.1 Wildcard DNS (Recommended Setup)

Instead of creating a DNS record per app, configure a **wildcard DNS record** once:

```
*.apps.example.com.    A    <server-ip>
apps.example.com.      A    <server-ip>
```

This means *any* subdomain of `apps.example.com` resolves to the server. When AppGarden deploys a new app and creates its `.caddy` file with the explicit domain, Caddy obtains a TLS certificate automatically on reload — no per-app DNS changes needed.

### 8.2 Setup Instructions

The README should include instructions for common DNS providers:

**Porkbun:**
1. Go to Domain Management → DNS Records
2. Add record: Type `A`, Host `*.apps`, Answer `<server-ip>`
3. Add record: Type `A`, Host `apps`, Answer `<server-ip>`

**Cloudflare:**
1. Go to DNS → Records
2. Add record: Type `A`, Name `*.apps`, IPv4 `<server-ip>`, Proxy status: DNS only
3. Add record: Type `A`, Name `apps`, IPv4 `<server-ip>`

**Namecheap, Google Domains, etc.**: Similar pattern — add a wildcard A record for `*.apps` (or `*` for the entire domain).

### 8.3 Non-Wildcard Fallback

If the user prefers not to use wildcard DNS, they can manually add A records per subdomain. AppGarden will still work — it just requires manual DNS setup before each subdomain deployment. The `appgarden deploy` command should detect and warn if the DNS doesn't resolve to the expected server.

## 9. Environments

Environments are a way to deploy the same app to different URLs with potentially different configurations. They are defined in the project-level `appgarden.toml`:

```toml
[app]
name = "mywebsite"
method = "dockerfile"
port = 3000

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
```

Each environment deploys as a separate app on the server, with a name derived from `<app-name>-<environment>` (e.g., `mywebsite-production`, `mywebsite-staging`). The `production` environment can optionally omit the suffix (just `mywebsite`).

Usage:

```bash
# Deploy production
appgarden deploy production

# Deploy staging
appgarden deploy staging

# Check status of all environments
appgarden status mywebsite --all-envs

# Deploy all environments
appgarden deploy --all-envs
```

When no `appgarden.toml` is present, environments are not used — apps are deployed directly with CLI flags.

## 10. Localhost Tunneling

This feature exposes a locally running app through the remote server, making it accessible at a public URL with HTTPS. Useful for demos, testing webhooks, or sharing work-in-progress.

### 10.1 How It Works

1. **Allocate a port** on the remote server from the port registry
2. **Create a temporary Caddy config** on the remote server:
   ```
   myapp.apps.example.com {
       reverse_proxy localhost:<allocated-port>
   }
   ```
3. **Reload Caddy** on the remote server (Caddy obtains a TLS certificate for the domain)
4. **Open an SSH reverse tunnel** from the remote server to the local machine:
   ```bash
   ssh -N -R <allocated-port>:localhost:<local-port> <ssh-user>@<server-ip>
   ```
5. **Display the public URL** to the user
6. **On exit** (Ctrl+C or `appgarden tunnel close`): close the SSH tunnel, remove the Caddy config, reload Caddy, deallocate the port

### 10.2 Requirements

The remote server's SSH config must allow TCP forwarding (`AllowTcpForwarding yes`, which is the default). `GatewayPorts` is *not* required because Caddy (running on the same server) connects to `localhost:<port>`.

### 10.3 Implementation

The tunnel command runs in the foreground, keeping the SSH connection alive. A signal handler cleans up on Ctrl+C. The tunnel state is recorded in `/srv/appgarden/tunnels/active.json` so that stale tunnels can be detected and cleaned up.

## 11. Port Management

AppGarden maintains a port registry at `/srv/appgarden/ports.json`:

```json
{
    "next_port": 10003,
    "allocated": {
        "10000": "myapp",
        "10001": "another-app",
        "10002": "tunnel-abc123"
    }
}
```

Ports are allocated starting from `10000` and increment. When an app is removed, its port is returned to the pool. Port conflicts are checked against active listeners on the server before allocation.

For Docker Compose apps, the user may manage their own port mappings. In this case, the user specifies `--port` to tell AppGarden which host port the app listens on, and this port is registered but not allocated.

## 12. Security

### 12.1 Server Hardening (via `server init`)

Following the patterns from `my-servers`:
- SSH: key-only auth, max 3 retries, no X11 forwarding
- UFW: default deny incoming, allow SSH/HTTP/HTTPS
- Fail2ban: protect SSH (ban after 3 failures, 1 hour ban)
- Automatic security updates via `unattended-upgrades`

### 12.2 App Isolation

- Docker-based apps run in containers with default isolation
- Each app has its own systemd service unit
- App files are owned by root with appropriate permissions
- No shared volumes between apps unless explicitly configured

### 12.3 Secrets Management

- Environment variables for apps are stored in `.env` files at `/srv/appgarden/apps/<name>/.env`
- These files have permissions `600` (readable only by root)
- Secrets are never logged or included in `garden.json`
- The `--env` and `--env-file` flags on the CLI handle secret passing

### 12.4 TLS

- Each app gets its own explicit Caddy config with its domain — Caddy obtains a TLS certificate via HTTP-01 challenge on config reload
- HTTP is automatically redirected to HTTPS by Caddy
- Only domains explicitly listed in `.caddy` files get certificates, preventing abuse

## 13. Remote State Files

### 13.1 `garden.json`

The central registry of all deployed apps:

```json
{
    "apps": {
        "myapp": {
            "name": "myapp",
            "method": "dockerfile",
            "url": "myapp.apps.example.com",
            "port": 10000,
            "source_type": "git",
            "source": "https://github.com/user/myapp.git",
            "branch": "main",
            "created_at": "2026-02-14T10:30:00Z",
            "updated_at": "2026-02-14T10:30:00Z",
            "environment": "production"
        }
    }
}
```

### 13.2 `ports.json`

Port allocation tracking (see Section 11).

### 13.3 `apps/<name>/app.json`

Per-app metadata, including deployment-specific details:

```json
{
    "name": "myapp",
    "method": "dockerfile",
    "port": 10000,
    "container_port": 3000,
    "url": "myapp.apps.example.com",
    "routing": "subdomain",
    "systemd_unit": "appgarden-myapp.service",
    "source_type": "git",
    "source": "https://github.com/user/myapp.git",
    "branch": "main",
    "auto_detected_runtime": "nodejs",
    "created_at": "2026-02-14T10:30:00Z"
}
```

## 14. Reading Remote State

Since there is no daemon, AppGarden reads remote state by running commands over SSH via pyinfra:

- **Read files**: `cat /srv/appgarden/garden.json` via pyinfra's `host.get_fact()` or direct shell commands
- **Check service status**: `systemctl is-active appgarden-<name>`
- **Check container status**: `docker compose ps` in the app directory
- **Check port usage**: `ss -tlnp` to verify ports are in use

AppGarden caches remote state locally during a CLI invocation to minimize SSH round-trips, but always reads fresh state at the start of each command.

## 15. Technical Implementation

### 15.1 Python Project Structure

```
appgarden/
├── pyproject.toml
├── README.md
├── PROJECT_SPEC.md
├── src/
│   └── appgarden/
│       ├── __init__.py
│       ├── cli.py                 # Click-based CLI entry point
│       ├── config.py              # Local config management (~/.config/appgarden/)
│       ├── server.py              # Server management operations
│       ├── deploy.py              # Deployment logic
│       ├── apps.py                # App lifecycle (start/stop/status/remove)
│       ├── tunnel.py              # Localhost tunneling
│       ├── routing.py             # Caddy config generation
│       ├── ports.py               # Port allocation
│       ├── remote.py              # Remote state read/write via pyinfra
│       ├── environments.py        # Environment handling
│       ├── auto_docker.py         # Auto Dockerfile generation
│       └── templates/
│           ├── systemd.service.j2
│           ├── docker-compose.yml.j2
│           ├── Dockerfile.j2
│           ├── Caddyfile.subdomain.j2
│           ├── Caddyfile.subdirectory.j2
│           └── Caddyfile.static.j2
└── tests/
```

### 15.2 Key Dependencies

- `click` — CLI framework
- `pyinfra` — Remote server operations over SSH
- `tomli` / `tomllib` — TOML config parsing
- `jinja2` — Template rendering for configs
- `rich` — Terminal output formatting (tables, progress, colors)

### 15.3 pyinfra Usage Patterns

Following the patterns from `my-servers`, AppGarden uses pyinfra programmatically (not via CLI orchestration). Example:

```python
from pyinfra.api import Config, Inventory, State
from pyinfra.api.operations import run_ops
from pyinfra import operations

# Build inventory from local config
inventory = Inventory(
    (["<server-ip>"], {}),
    ssh_user="root",
    ssh_key="~/.ssh/hcloud",
)

# Run operations
state = State(inventory, Config())
operations.files.put(src=local_path, dest=remote_path)
operations.systemd.service(service=f"appgarden-{name}", running=True, enabled=True)
run_ops(state)
```

Or, if programmatic use proves too complex, use the CLI-based approach via `ipyinfra`-style subprocess calls to `pyinfra`.

## 16. Workflow Examples

### Deploy a Node.js app from a git repo

```bash
appgarden deploy myapp \
  --method auto \
  --source https://github.com/user/myapp.git \
  --port 3000 \
  --cmd "npm start" \
  --url myapp.apps.example.com
```

AppGarden will: detect Node.js from `package.json`, generate a Dockerfile with `npm install` and `npm start`, build the image on the server, create a systemd service, configure Caddy for the subdomain, and start the app.

### Deploy a Docker Compose stack

```bash
appgarden deploy mystack \
  --method docker-compose \
  --source ./my-compose-project/ \
  --port 8080 \
  --url mystack.apps.example.com \
  --env-file .env.production
```

### Deploy a static site to a subdirectory

```bash
appgarden deploy docs \
  --method static \
  --source ./build/ \
  --url apps.example.com/docs
```

### Quick tunnel for a local dev server

```bash
# In one terminal, your app is running on localhost:5173
appgarden tunnel 5173 --url demo.apps.example.com
# → Your app is now live at https://demo.apps.example.com
# → Press Ctrl+C to close the tunnel
```

### Multi-environment deployment

```bash
# With appgarden.toml in project root:
appgarden deploy production    # deploys main branch to myapp.apps.example.com
appgarden deploy staging       # deploys staging branch to myapp-staging.apps.example.com

appgarden status myapp --all-envs
# NAME                  ENV         URL                                STATUS
# myapp                 production  myapp.apps.example.com             running
# myapp-staging         staging     myapp-staging.apps.example.com     running
```

## 17. Future Considerations

These are out of scope for v1 but worth noting:

- **CI/CD integration**: GitHub Actions / GitLab CI workflows that call `appgarden deploy`
- **Health checks**: Periodic HTTP checks with alerting
- **Rollback**: Keep previous deployment versions and allow rollback
- **Resource limits**: CPU/memory limits for Docker containers
- **Multi-server**: Deploy the same app to multiple servers (load balancing)
- **DNS provider plugins**: Optional automatic DNS record management (Porkbun, Cloudflare, etc.) for users who want it
- **Log aggregation**: Centralised logging across apps
- **Backup/restore**: Automated backups of app data volumes
