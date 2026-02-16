---
name: appgarden
description: >
  Deploy and manage web applications on remote servers with AppGarden.
  Use when the user asks about deploying apps, managing servers, configuring
  appgarden.toml, or troubleshooting deployed applications.
user-invocable: false
---

# AppGarden — Agent Skill Guide

Use this guide when deploying web applications to remote servers with `appgarden`.

## Prerequisites

Before deploying, you need:

1. **A configured server** — check with `appgarden server list`. If none exist, add one:
   ```bash
   appgarden server add myserver \
     --host <server-ip> \
     --ssh-user root \
     --ssh-key ~/.ssh/id_rsa \
     --domain apps.example.com
   ```
   For Hetzner Cloud, use `--hcloud-name` and `--hcloud-context` instead of `--host`.

2. **An initialized server** — run `appgarden server init` once per server. This installs Docker, Caddy, configures the firewall, and creates the AppGarden directory structure.

3. **Wildcard DNS** — a `*.apps.example.com` A record pointing to the server IP. This lets any subdomain resolve automatically without per-app DNS setup.

## Deploying with `appgarden.toml` (Recommended)

Create `appgarden.toml` in the project root to define deployment targets declaratively.

### Minimal example

```toml
[app]
name = "myapp"
method = "dockerfile"
container_port = 3000
source = "."

[environments.production]
subdomain = "myapp"

[environments.staging]
subdomain = "myapp-staging"
branch = "staging"
```

Then deploy:

```bash
appgarden deploy production       # Deploys as "myapp" at myapp.<server-domain>
appgarden deploy staging          # Deploys as "myapp-staging" at myapp-staging.<server-domain>
appgarden deploy --all-envs       # Deploys all environments
```

### URL construction

Environments can specify the URL in three ways (in order of preference):

| Field | Example | Result |
|-------|---------|--------|
| `subdomain = "myapp"` | Combined with server domain | `myapp.apps.example.com` |
| `path = "api"` | Subdirectory on server domain | `apps.example.com/api` |
| `url = "custom.example.com"` | Explicit full URL | `custom.example.com` |

`subdomain` and `path` are preferred over `url` because they adapt to whichever server is used. Use `domain` to override the base domain if it differs from the server's.

### Configuration cascade

Values are resolved in this priority: **CLI flags > environment > [app] defaults > global defaults > hardcoded defaults**.

All fields from `[app]` are inherited by every environment unless overridden. This means shared settings like `method`, `source`, and `container_port` only need to be specified once.

### `appgarden.toml` fields reference

**`[app]` section** (shared defaults):

| Field | Description |
|-------|-------------|
| `name` | App base name (required) |
| `method` | `static`, `command`, `dockerfile`, `docker-compose`, `auto` |
| `source` | Local path or git URL |
| `container_port` | Port the app listens on inside the container |
| `cmd` | Start command (for `command`/`auto` methods) |
| `setup_cmd` | Build/install command (for `auto` method) |
| `subdomain` | Default subdomain (can be overridden per environment) |
| `path` | Default path prefix |
| `domain` | Override base domain |
| `port` | Host port (auto-allocated if omitted) |
| `env` | Inline env vars: `{ KEY = "value" }` |
| `meta` | Arbitrary metadata: `{ team = "backend", visibility = "internal" }` |

**`[environments.<name>]` sections** — same fields as `[app]` plus:

| Field | Description |
|-------|-------------|
| `server` | Which configured server to deploy to |
| `branch` | Git branch (for git sources) |
| `env_file` | Path to .env file |

Both `env` and `meta` are merged across levels (environment overrides app defaults per-key).

## Deploying without `appgarden.toml`

For one-off deployments, pass everything via CLI flags:

```bash
# Static site
appgarden deploy mysite --method static --source ./dist/ --subdomain mysite

# Dockerfile
appgarden deploy myapp --method dockerfile --source . --container-port 3000 --subdomain myapp

# Auto-detect runtime
appgarden deploy myapp --method auto --source . --cmd "npm start" --subdomain myapp

# Bare command (no Docker)
appgarden deploy myapi --method command --cmd "python app.py" --source ./api/ --subdomain myapi

# Explicit host port (instead of auto-allocation)
appgarden deploy myapp --method dockerfile --source . --port 8080 --container-port 3000 --subdomain myapp

# Docker Compose
appgarden deploy mystack --method docker-compose --source ./project/ --subdomain mystack

# With metadata
appgarden deploy myapp --method dockerfile --source . --subdomain myapp \
  --meta team=backend --meta visibility=internal
```

## Deployment methods

| Method | When to use | Required |
|--------|-------------|----------|
| `static` | HTML/CSS/JS sites, SPAs | `source` |
| `dockerfile` | Projects with a Dockerfile | `source` |
| `docker-compose` | Projects with docker-compose.yml | `source` |
| `auto` | Any project (generates Dockerfile) | `source`, `cmd` |
| `command` | Run a process directly via systemd | `cmd` |

## Managing deployed apps

```bash
appgarden apps list                      # List all apps on the default server
appgarden apps status myapp              # Detailed status (URL, method, port, metadata)
appgarden apps logs myapp -n 100         # View logs
appgarden apps restart myapp             # Restart
appgarden apps redeploy myapp            # Pull latest source, rebuild, restart
appgarden apps stop myapp                # Stop
appgarden apps start myapp               # Start
appgarden apps remove myapp --yes        # Remove app and all resources
appgarden apps remove myapp --keep-data  # Remove but preserve data/ directory
```

Use `--server <name>` on any command to target a specific server.

## App metadata

Attach arbitrary key-value metadata to apps:

```bash
# View metadata
appgarden apps meta get myapp

# Set/update individual keys
appgarden apps meta set myapp --meta team=backend --meta tier=premium

# Replace all metadata with a JSON object
appgarden apps meta replace myapp --json '{"team": "frontend"}'

# Remove specific keys
appgarden apps meta remove myapp tier visibility
```

In `appgarden.toml`, metadata merges like env vars (environment overrides app defaults per-key):

```toml
[app]
name = "myapp"
meta = { team = "backend", visibility = "internal" }

[environments.production]
meta = { visibility = "public" }
# result: { team = "backend", visibility = "public" }
```

## Environment variables

```bash
# Inline
appgarden deploy myapp ... --env DATABASE_URL=postgres://... --env SECRET_KEY=abc

# From file
appgarden deploy myapp ... --env-file .env.production
```

In `appgarden.toml`:
```toml
[environments.production]
env = { DATABASE_URL = "postgres://...", NODE_ENV = "production" }
env_file = ".env.production"
```

Environment variables from `[app].env` and `[environments.<name>].env` are merged (environment-level overrides app-level).

## Localhost tunneling

Expose a local dev server through the remote server with automatic HTTPS:

```bash
appgarden tunnel open 3000 --url myapp.apps.example.com
# App is now live at https://myapp.apps.example.com
# Press Ctrl+C to close

appgarden tunnel list                    # List active tunnels
appgarden tunnel close <tunnel-id>       # Close a specific tunnel
appgarden tunnel cleanup                 # Remove stale tunnels
```

## Server management

```bash
appgarden server add <name> --host <ip> --domain <domain> [--ssh-user root] [--ssh-key ~/.ssh/id_rsa] [--app-root /srv/appgarden]
appgarden server list
appgarden server ping <name>             # Test SSH connectivity
appgarden server init <name>             # Install Docker, Caddy, firewall, etc.
appgarden server init --skip docker      # Skip specific steps
appgarden server init --minimal          # Only essential steps
appgarden server default <name>          # Set default server
appgarden server remove <name>           # Remove from local config only
```

Init steps that can be skipped: `update`, `docker`, `caddy`, `firewall`, `ssh`, `fail2ban`, `upgrades`.

## Utilities

```bash
appgarden version                        # Show appgarden version
appgarden config show                    # Print the current config file
```

## Common workflows

### Deploy a new project

1. Create `appgarden.toml` with app config and environments
2. Run `appgarden deploy production`
3. Verify with `appgarden apps status <app-name>`

### Update a deployed app

```bash
appgarden apps redeploy myapp       # Pulls latest source, rebuilds, restarts
```

### Check what's running

```bash
appgarden apps list                 # Overview of all apps
appgarden apps status myapp         # Detailed info for one app
appgarden apps logs myapp           # Recent logs
```

### Debug a failing app

```bash
appgarden apps logs myapp -n 200    # Check logs
appgarden apps restart myapp        # Try restarting
appgarden apps status myapp         # Check if it came back up
```

## Architecture notes

- **Agentless** — no daemon on the server; everything runs locally over SSH via pyinfra
- **Remote state** — app registry at `/srv/appgarden/garden.json`, port allocation at `/srv/appgarden/ports.json`
- **Caddy** — each app gets a `.caddy` config file; Caddy obtains TLS certificates automatically via HTTP-01
- **Systemd** — non-static apps run as `appgarden-<name>.service` units
- **Ports** — auto-allocated starting from 10000
- **App files** — stored at `/srv/appgarden/apps/<name>/` on the server
