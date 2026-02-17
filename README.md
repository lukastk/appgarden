# AppGarden

Deploy web applications to remote servers.

AppGarden manages a "garden" of web applications on remote servers. It handles deployment, routing, TLS certificates, and lifecycle management — all without requiring a persistent daemon on the server. State is encoded in the filesystem and configuration files on the remote, and all operations are performed over SSH using `pyinfra`.

## Installation

```bash
pip install appgarden
```

Or with `uv`:

```bash
uv pip install appgarden
```

## Quick Start

### 1. Add a server

```bash
appgarden server add myserver \
  --host 203.0.113.10 \
  --ssh-user root \
  --ssh-key ~/.ssh/id_rsa \
  --domain apps.example.com
```

Or with Hetzner Cloud:

```bash
appgarden server add myserver \
  --hcloud-name main \
  --hcloud-context my-project \
  --ssh-user root \
  --ssh-key ~/.ssh/hcloud \
  --domain apps.example.com
```

### 2. Initialize the server

```bash
appgarden server init myserver
```

This installs Docker, Caddy, configures UFW, SSH hardening, fail2ban, and sets up the AppGarden directory structure.

### 3. Deploy an app

```bash
# Static site
appgarden deploy mysite \
  --method static \
  --source ./dist/ \
  --url mysite.apps.example.com

# Docker app (auto-detects runtime)
appgarden deploy myapp \
  --method auto \
  --source ./my-project/ \
  --cmd "npm start" \
  --url myapp.apps.example.com

# Dockerfile
appgarden deploy myapp \
  --method dockerfile \
  --source ./my-project/ \
  --container-port 8080 \
  --url myapp.apps.example.com

# Bare command
appgarden deploy myapi \
  --method command \
  --cmd "python app.py" \
  --source ./api/ \
  --url myapi.apps.example.com
```

### 4. Manage apps

```bash
appgarden apps list
appgarden apps status myapp
appgarden apps logs myapp -n 100
appgarden apps restart myapp
appgarden apps redeploy myapp
appgarden apps remove myapp
```

## DNS Setup

For subdomain-based routing (recommended), configure a **wildcard DNS record**:

```
*.apps.example.com.    A    <server-ip>
apps.example.com.      A    <server-ip>
```

### Porkbun

1. Go to Domain Management > DNS Records
2. Add record: Type `A`, Host `*.apps`, Answer `<server-ip>`
3. Add record: Type `A`, Host `apps`, Answer `<server-ip>`

### Cloudflare

1. Go to DNS > Records
2. Add record: Type `A`, Name `*.apps`, IPv4 `<server-ip>`, Proxy status: **DNS only**
3. Add record: Type `A`, Name `apps`, IPv4 `<server-ip>`

**Important:** Cloudflare's proxy (orange cloud) only supports wildcard DNS on Enterprise plans. Use "DNS only" (grey cloud) for the wildcard record.

### Namecheap

1. Go to Advanced DNS
2. Add record: Type `A`, Host `*.apps`, Value `<server-ip>`
3. Add record: Type `A`, Host `apps`, Value `<server-ip>`

Any new subdomain deployed by AppGarden will automatically resolve to your server, and Caddy will obtain TLS certificates on demand.

## Deployment Methods

| Method | Use case | How it works |
|--------|----------|-------------|
| `static` | Static sites, SPAs | Uploads files, Caddy serves them directly |
| `command` | Any process | Runs a command via systemd, Caddy reverse-proxies |
| `dockerfile` | Docker apps | Builds image, generates docker-compose, runs via systemd |
| `docker-compose` | Multi-container apps | Uses your docker-compose.yml, runs via systemd |
| `auto` | Auto-detect runtime | Detects Node.js/Python/Go/Ruby/Rust, generates Dockerfile |

### Static Sites

```bash
appgarden deploy docs --method static --source ./site/ --url docs.apps.example.com
```

Supports git sources too:

```bash
appgarden deploy docs --method static \
  --source https://github.com/user/site.git \
  --branch gh-pages \
  --url docs.apps.example.com
```

### Docker Apps

From a Dockerfile:

```bash
appgarden deploy webapp --method dockerfile \
  --source ./app/ \
  --container-port 8080 \
  --url webapp.apps.example.com \
  --env SECRET_KEY=abc123
```

From an existing docker-compose.yml:

```bash
appgarden deploy stack --method docker-compose \
  --source ./project/ \
  --url stack.apps.example.com
```

### Auto-Docker

Auto-detects runtime from project files (package.json, requirements.txt, go.mod, etc.), generates a Dockerfile, builds and deploys:

```bash
appgarden deploy myapp --method auto \
  --source ./project/ \
  --cmd "node server.js" \
  --url myapp.apps.example.com
```

Supported runtimes: Node.js, Python (pip), Python (pyproject.toml), Go, Ruby, Rust.

### Command

Run any process directly via systemd (no Docker):

```bash
appgarden deploy api --method command \
  --cmd "python -m uvicorn main:app --host 0.0.0.0 --port \$PORT" \
  --source ./api/ \
  --url api.apps.example.com
```

The `PORT` environment variable is automatically set to the allocated port.

## Environment Variables

Pass environment variables with `--env` (repeatable) or `--env-file`:

```bash
appgarden deploy myapp --method dockerfile \
  --source . \
  --url myapp.apps.example.com \
  --env DATABASE_URL=postgres://... \
  --env SECRET_KEY=abc123

# Or from a file
appgarden deploy myapp --method dockerfile \
  --source . \
  --url myapp.apps.example.com \
  --env-file .env.production
```

Environment files are stored on the server with `600` permissions (readable only by root).

All three sources can be combined. When duplicate keys exist, the precedence order is:

1. `env_file` (base — loaded first)
2. `appgarden.toml` `env` (overrides file)
3. CLI `--env` flags (highest priority)

## App Metadata

Attach arbitrary key-value metadata to apps for organization and tracking:

```bash
# Set metadata during deploy
appgarden deploy myapp --method dockerfile \
  --source . \
  --url myapp.apps.example.com \
  --meta team=backend \
  --meta visibility=internal

# View metadata
appgarden apps meta get myapp

# Update individual keys
appgarden apps meta set myapp --meta tier=premium --meta visibility=public

# Replace all metadata
appgarden apps meta replace myapp --json '{"team": "frontend", "tier": "free"}'

# Remove specific keys
appgarden apps meta remove myapp tier visibility
```

Metadata is also shown in `appgarden apps status`. Metadata can also be set in `appgarden.toml` — see the [reference below](#appgardentoml-reference).

## Environments (appgarden.toml)

For projects with multiple deployment targets, create an `appgarden.toml` in your project root:

```toml
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
```

Then deploy by environment name:

```bash
# Deploy production
appgarden deploy production

# Deploy staging
appgarden deploy staging

# Deploy all environments
appgarden deploy --all-envs
```

App names are derived automatically: `mywebsite` for production, `mywebsite-staging` for staging.

### appgarden.toml Reference

All fields can be set at the `[app]` level (inherited by all environments) and overridden per `[environments.<name>]`. The cascade order is: hardcoded defaults < global config < `[app]` defaults < environment overrides < CLI flags.

```toml
[app]
name = "mywebsite"          # Required. Base app name.
slug = "my-website"         # Optional. Used in {app.slug} placeholders (falls back to name).
server = "myserver"         # Server name from appgarden config.
method = "dockerfile"       # Deployment method: static, command, dockerfile, docker-compose, auto.
source = "."                # Local path or git URL.
url = "myapp.example.com"   # Full URL for the app.
subdomain = "{app.slug}"    # Subdomain (combined with domain). Supports placeholders.
path = "api"                # Path prefix (combined with domain). Alternative to subdomain.
domain = "example.com"      # Base domain (overrides server domain for subdomain/path).
port = 10000                # Host port (auto-allocated if omitted).
container_port = 3000       # Container port (for dockerfile/auto methods). Default: 3000.
cmd = "npm start"           # Start command (for command/auto methods).
setup_cmd = "npm ci"        # Setup/install command (for auto method).
branch = "main"             # Git branch (for git sources).
env_file = ".env.production"  # Path to .env file (relative to project dir). Overridden by env/--env.
gitignore = true            # Filter uploads using .gitignore (default: true).

# Dict fields — merged (env-level overrides app-level keys):
env = { NODE_ENV = "production", LOG_LEVEL = "info" }
meta = { team = "backend", visibility = "internal" }

# List fields — concatenated and deduplicated across layers:
exclude = ["node_modules", ".git"]
volumes = ["./data:/app/data", "/var/logs:/app/logs:ro"]

[environments.production]
url = "mywebsite.apps.example.com"
branch = "main"
env = { NODE_ENV = "production" }         # merged with [app] env
meta = { visibility = "public" }          # merged with [app] meta
volumes = ["/var/certs:/app/certs:ro"]    # concatenated with [app] volumes

[environments.staging]
url = "mywebsite-staging.apps.example.com"
branch = "staging"
env = { NODE_ENV = "staging" }
```

**Placeholders:** String values support `{app.name}`, `{app.slug}`, and `{env.name}` placeholders, interpolated per environment.

**Volumes:** Only apply to `dockerfile` and `auto` methods (template-generated compose). The `docker-compose` method uses your own compose file.

## Subdirectory Routing

Apps can be deployed to subdirectories instead of subdomains:

```bash
appgarden deploy docs --method static \
  --source ./docs/ \
  --url apps.example.com/docs

appgarden deploy api --method command \
  --cmd "python app.py" \
  --source ./api/ \
  --url apps.example.com/api
```

Multiple subdirectory apps on the same domain share a single Caddy config file. The `X-Forwarded-Prefix` header is set so your app knows its base path.

**Framework-specific BASE_URL settings:**

| Framework | Setting |
|-----------|---------|
| Next.js | `basePath: "/docs"` in `next.config.js` |
| React Router | `<BrowserRouter basename="/docs">` |
| Flask | `APPLICATION_ROOT = "/docs"` |
| FastAPI | `app = FastAPI(root_path="/docs")` |
| Django | `FORCE_SCRIPT_NAME = "/docs"` |

## Localhost Tunneling

Expose a locally running app through your server with HTTPS:

```bash
# Start a tunnel (blocks until Ctrl+C)
appgarden tunnel open 3000 --url myapp.apps.example.com

# List active tunnels
appgarden tunnel list

# Close a specific tunnel
appgarden tunnel close tunnel-abc12345

# Clean up stale tunnels
appgarden tunnel cleanup
```

This opens an SSH reverse tunnel and configures Caddy as a reverse proxy with automatic HTTPS. Useful for demos, webhook testing, or sharing work-in-progress.

## CLI Reference

### Global Flags

```
--verbose, -v    Show detailed output
--quiet, -q      Suppress non-essential output
--version        Show version and exit
```

### Server Management

```bash
appgarden server add <name> --host <ip> --domain <domain> [--ssh-user root] [--ssh-key ~/.ssh/id_rsa]
appgarden server add <name> --hcloud-name <name> --hcloud-context <ctx> --domain <domain>
appgarden server list
appgarden server remove <name>
appgarden server default <name>
appgarden server init [name]
appgarden server ping [name]
```

### Deployment

```bash
appgarden deploy <name> --method <method> --url <url> [options]
appgarden deploy <env-name>           # From appgarden.toml
appgarden deploy --all-envs           # All environments
```

### App Lifecycle

```bash
appgarden apps list [-s server]
appgarden apps status <name> [-s server]
appgarden apps start <name>
appgarden apps stop <name>
appgarden apps restart <name>
appgarden apps logs <name> [-n 50]
appgarden apps remove <name> [--keep-data] [--yes]
appgarden apps redeploy <name>
```

### App Metadata

```bash
appgarden apps meta get <name> [-s server]
appgarden apps meta set <name> --meta KEY=VALUE [--meta ...] [-s server]
appgarden apps meta replace <name> --json '{"key": "value"}' [-s server]
appgarden apps meta remove <name> KEY [KEY ...] [-s server]
```

### Tunnels

```bash
appgarden tunnel open <local-port> --url <url> [-s server]
appgarden tunnel list [-s server]
appgarden tunnel close <tunnel-id> [-s server]
appgarden tunnel cleanup [-s server]
```

### Configuration

```bash
appgarden config show
appgarden version
```

## Architecture

- **Agentless**: No daemon on the server. All operations run locally via pyinfra over SSH.
- **Remote state**: App registry stored on server at `/srv/appgarden/garden.json`.
- **Caddy**: Each app gets a `.caddy` config file; Caddy obtains TLS certificates automatically.
- **Port allocation**: Starting from port 10000, managed via `/srv/appgarden/ports.json`.
- **Systemd**: Non-static apps run as systemd services for automatic restarts and log management.

## Non-Root User Setup

By default, the quick start examples use `root` as the SSH user. For production servers, it's recommended to create a dedicated deploy user with restricted privileges. AppGarden includes a privileged wrapper script that limits sudo access to only the operations needed for deployment.

### 1. Create a deploy user on the server

SSH into your server as root and create a user:

```bash
useradd -m -s /bin/bash appgarden-deploy
```

### 2. Set up SSH key authentication

Copy your SSH public key to the new user:

```bash
# From your local machine
ssh-copy-id -i ~/.ssh/id_rsa appgarden-deploy@<server-ip>
```

Or manually:

```bash
# On the server as root
mkdir -p /home/appgarden-deploy/.ssh
cp ~/.ssh/authorized_keys /home/appgarden-deploy/.ssh/authorized_keys
chown -R appgarden-deploy:appgarden-deploy /home/appgarden-deploy/.ssh
chmod 700 /home/appgarden-deploy/.ssh
chmod 600 /home/appgarden-deploy/.ssh/authorized_keys
```

### 3. Add the server with the appgarden-deploy user

```bash
appgarden server add myserver \
  --host <server-ip> \
  --ssh-user appgarden-deploy \
  --ssh-key ~/.ssh/id_rsa \
  --domain apps.example.com
```

### 4. Initialize the server (as root)

Server init requires full sudo access, so run it once as root (or a user with `NOPASSWD: ALL` sudoers access):

```bash
# Temporarily add the server with root access for init
appgarden server add myserver-init \
  --host <server-ip> \
  --ssh-user root \
  --ssh-key ~/.ssh/id_rsa \
  --domain apps.example.com

appgarden server init myserver-init --include group
appgarden server remove myserver-init
```

This installs Docker, Caddy, creates the `appgarden` group, installs the privileged wrapper script at `/usr/local/bin/appgarden-privileged`, and configures a sudoers entry that grants the `appgarden` group passwordless sudo for **only** that wrapper.

### 5. Add the appgarden-deploy user to the appgarden group

On the server as root:

```bash
usermod -aG appgarden,docker appgarden-deploy
```

The user needs to log out and back in for the group membership to take effect. The `docker` group is needed for Docker-based deployment methods.

### 6. Deploy as the non-root user

All subsequent deploys use the restricted appgarden-deploy user:

```bash
appgarden deploy myapp \
  --method dockerfile \
  --source ./app/ \
  --url myapp.apps.example.com
```

### How it works

The privileged wrapper (`appgarden-privileged`) only allows:

- `systemctl` operations on `appgarden-*.service` units (start, stop, restart, enable, disable, is-active, daemon-reload)
- `systemctl reload caddy`
- Installing/removing systemd unit files matching `appgarden-*.service`
- `journalctl` for `appgarden-*.service` units

All inputs are validated against strict patterns — no shell interpretation, no path traversal, no access to non-appgarden services. Root users bypass the wrapper entirely and execute commands directly.

### Adding more deploy users

```bash
# On the server as root
useradd -m -s /bin/bash newuser
usermod -aG appgarden newuser
usermod -aG docker newuser  # if deploying Docker apps
```

## Security

- SSH key-only authentication, hardened sshd config
- UFW firewall: default deny, allow SSH/HTTP/HTTPS
- Fail2ban for SSH brute-force protection
- Automatic security updates via unattended-upgrades
- Privileged wrapper restricts non-root users to appgarden-scoped operations only
- Environment files stored with 600 permissions
- Docker isolation for container-based apps
- TLS via Caddy's automatic HTTPS (HTTP-01 challenge)

## License

MIT
