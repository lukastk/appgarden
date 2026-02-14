# AppGarden — Implementation Plan

## Testing Strategy

Tests are split into two categories:

### Unit Tests

Run by default with `uv run pytest`. These test pure logic with no remote operations:
config parsing, port allocation, template rendering, URL parsing, runtime detection, environment parsing.

### Integration Tests

Run only with `uv run pytest -m integration`. These dynamically provision a Hetzner Cloud server via `hcloud`, run real deployments against it, and tear it down afterwards.

Configuration is read from `.env` in the repo root (not committed). An `.env.sample` documents the required variables:

```bash
# .env.sample — Copy to .env and fill in values

# Hetzner Cloud
APPGARDEN_TEST_HCLOUD_CONTEXT=my-project      # hcloud CLI context to use
APPGARDEN_TEST_HCLOUD_SSH_KEY=my-key-name      # Name of SSH key registered in hcloud
APPGARDEN_TEST_SSH_KEY_PATH=~/.ssh/hcloud      # Local path to private SSH key
APPGARDEN_TEST_SERVER_TYPE=cx22                # Server type (cx22 = 2 vCPU, 4GB RAM)
APPGARDEN_TEST_LOCATION=fsn1                   # Datacenter location
APPGARDEN_TEST_IMAGE=ubuntu-24.04              # OS image
```

The integration test infrastructure:

- A **session-scoped pytest fixture** provisions a server at the start of the test session and deletes it at the end (via `hcloud server create` / `hcloud server delete`)
- The server is initialised with `appgarden server init` as part of the fixture
- A `pytest.ini` marker `integration` is registered in `pyproject.toml`
- Tests are in `pts/tests/integration/` (separate from unit tests)
- The fixture is defined in `pts/tests/integration/conftest.pct.py`


## Phase 1: Project Scaffolding

Set up the nblite project structure, build system, and a minimal working CLI.

### Tasks

- [ ] Create `nblite.toml` with export pipeline (`nbs -> pts -> lib`, `nbs_tests -> pts_tests -> lib_tests`)
- [ ] Create `pyproject.toml` with hatchling build, dependencies (`click`, `pyinfra`, `jinja2`, `rich`, `tomli`), and `[project.scripts] appgarden = "appgarden.cli:main"`
- [ ] Create `pts/appgarden/10_cli.pct.py` with a minimal Click CLI skeleton (`appgarden --help`, `appgarden version`)
- [ ] Create `src/appgarden/templates/` directory for static Jinja2 templates
- [ ] Run `nbl export` and verify the CLI works: `uv run appgarden --help`
- [ ] Create `README.md` with project description and DNS setup instructions (wildcard DNS for common providers)
- [ ] Create `.env.sample` with integration test configuration variables
- [ ] Add `.env` to `.gitignore`
- [ ] Register `integration` marker in `pyproject.toml` pytest config

### Tests

- [ ] `appgarden --help` exits 0 and prints usage
- [ ] `appgarden version` prints the version string


## Phase 2: Local Configuration

Implement local config management (`~/.config/appgarden/config.toml`).

### Tasks

- [ ] Create `pts/appgarden/00_config.pct.py`:
  - Load/save/create config at `~/.config/appgarden/config.toml`
  - Server config dataclass/model: `host`, `ssh_user`, `ssh_key`, `domain`, optional `hcloud_name`, `hcloud_context`
  - Resolve `hcloud` server names to IPs via `hcloud --context <ctx> server describe <name> -o json`
  - `default_server` setting
- [ ] Add CLI commands in `pts/appgarden/10_cli.pct.py`:
  - `appgarden server add <name>` (with `--host`/`--hcloud-name`, `--ssh-user`, `--ssh-key`, `--domain`)
  - `appgarden server list`
  - `appgarden server remove <name>`
  - `appgarden server default <name>`
  - `appgarden config show`

### Tests

- [ ] `test_config.pct.py`: Config load/save round-trip with temp directory
- [ ] `test_config.pct.py`: Server add/remove/list operations
- [ ] `test_config.pct.py`: hcloud IP resolution (mock subprocess call)
- [ ] `test_config.pct.py`: Default server get/set


## Phase 3: Remote Operations & Port Management

Build the core layer for reading/writing state on the remote server via pyinfra, and port allocation.

### Tasks

- [ ] Create `pts/appgarden/01_remote.pct.py`:
  - SSH connection helper using pyinfra (build inventory from server config)
  - `read_remote_file(server, path) -> str` — read a file on the remote
  - `write_remote_file(server, path, content)` — write a file on the remote
  - `run_remote_command(server, cmd) -> str` — run a command, return stdout
  - `read_garden_state(server) -> dict` — read `/srv/appgarden/garden.json`
  - `write_garden_state(server, state)` — write garden.json
  - `upload_directory(server, local_path, remote_path)` — rsync/put a directory
- [ ] Create `pts/appgarden/02_ports.pct.py`:
  - `read_ports(server) -> dict` — read `/srv/appgarden/ports.json`
  - `allocate_port(server, app_name) -> int` — allocate next available port
  - `release_port(server, app_name)` — free a port
  - `register_port(server, port, app_name)` — register a user-specified port

### Tests

- [ ] `test_ports.pct.py`: Port allocation increments correctly
- [ ] `test_ports.pct.py`: Port release returns port to pool
- [ ] `test_ports.pct.py`: Port conflicts detected
- [ ] `test_remote.pct.py`: Garden state read/write round-trip (mock)


## Phase 4: Server Initialization

Implement `appgarden server init` to prepare a fresh server.

### Tasks

- [ ] Create `pts/appgarden/04_server.pct.py`:
  - `init_server(server_config)` function that uses pyinfra to:
    - `apt update && apt upgrade`
    - Install Docker CE (official repo + `docker-compose-plugin`)
    - Install Caddy (official repo)
    - Create root Caddyfile: `import /srv/appgarden/caddy/apps/*.caddy` and `import /srv/appgarden/caddy/tunnels/*.caddy`
    - Configure UFW: default deny, allow SSH/HTTP/HTTPS
    - SSH hardening: `/etc/ssh/sshd_config.d/hardening.conf`
    - Install & configure fail2ban
    - Install `unattended-upgrades`
    - Create directory structure: `/srv/appgarden/{apps,caddy/apps,caddy/tunnels,tunnels}`
    - Initialise `garden.json` and `ports.json`
  - `ping_server(server_config) -> bool` — test SSH connectivity
- [ ] Add CLI commands:
  - `appgarden server init <name>`
  - `appgarden server ping <name>`
- [ ] Create `pts/tests/integration/conftest.pct.py`:
  - Load `.env` from repo root (using `python-dotenv` or manual parsing)
  - Session-scoped `hcloud_server` fixture:
    - `hcloud --context <ctx> server create --name appgarden-test-<random> --type <type> --location <loc> --image <image> --ssh-key <key>`
    - Wait for server to be ready (poll `hcloud server describe` for status `running`)
    - Yield server IP
    - Teardown: `hcloud --context <ctx> server delete appgarden-test-<random>`
  - Session-scoped `initialized_server` fixture (depends on `hcloud_server`):
    - Run `appgarden server init` against the provisioned server
    - Yield server config

### Tests

- [ ] `test_server.pct.py`: `ping_server` with mock SSH (success and failure)
- [ ] `test_server.pct.py`: `init_server` generates correct pyinfra operations (mock/dry-run)
- [ ] `integration/test_server_init.pct.py`: Provision server, run `server init`, verify Docker/Caddy/UFW are installed and AppGarden directory structure exists


## Phase 5: Caddy Routing & Templates

Implement Caddy config generation for subdomain and subdirectory routing.

### Tasks

- [ ] Create Jinja2 templates in `src/appgarden/templates/`:
  - `Caddyfile.subdomain.j2`:
    ```
    {{ domain }} {
        reverse_proxy localhost:{{ port }}
    }
    ```
  - `Caddyfile.subdirectory.j2`:
    ```
    {{ domain }} {
        redir /{{ path }} /{{ path }}/
        handle_path /{{ path }}/* {
            reverse_proxy localhost:{{ port }} {
                header_up X-Forwarded-Prefix "/{{ path }}"
            }
        }
    }
    ```
  - `Caddyfile.static.j2`:
    ```
    {{ domain }} {
        root * {{ source_path }}
        file_server
        try_files {path} /index.html
    }
    ```
  - `systemd.service.j2` (parameterised service unit)
  - `docker-compose.yml.j2` (for wrapping Dockerfile builds)
  - `Dockerfile.j2` (for auto-docker)
- [ ] Create `pts/appgarden/03_routing.pct.py`:
  - `parse_url(url) -> (domain, path_or_none)` — determine if subdomain or subdirectory
  - `generate_caddy_config(app) -> str` — render the appropriate template
  - `deploy_caddy_config(server, app_name, config)` — write `.caddy` file and reload Caddy
  - `remove_caddy_config(server, app_name)` — remove `.caddy` file and reload
  - Handle subdirectory merging: multiple apps on the same domain share one `.caddy` file

### Tests

- [ ] `test_routing.pct.py`: `parse_url` correctly identifies subdomains vs subdirectories
- [ ] `test_routing.pct.py`: Subdomain template renders correctly
- [ ] `test_routing.pct.py`: Subdirectory template renders correctly with path and X-Forwarded-Prefix
- [ ] `test_routing.pct.py`: Static site template renders correctly
- [ ] `test_routing.pct.py`: Multiple subdirectory apps on same domain merge into one `.caddy` file


## Phase 6: Deployment — Static Sites

Implement the simplest deployment method end-to-end.

### Tasks

- [ ] Create `pts/appgarden/05_deploy.pct.py`:
  - `deploy_static(server, name, source, url)`:
    - Upload source directory to `/srv/appgarden/apps/<name>/source/`
    - Generate and deploy Caddy config (subdomain or subdirectory)
    - Register app in `garden.json` with `app.json`
  - Source upload helper: detect local path vs git URL
  - Git clone on server if source is a URL
- [ ] Add CLI command: `appgarden deploy <name> --method static --source <path> --url <url>`
- [ ] Wire up the full flow: config → remote → routing → deploy

### Tests

- [ ] `test_deploy.pct.py`: Static deploy generates correct Caddy config
- [ ] `test_deploy.pct.py`: App is registered in garden.json after deploy
- [ ] `test_deploy.pct.py`: Source detection (local path vs git URL)
- [ ] `integration/test_deploy_static.pct.py`: Deploy a static site, verify Caddy serves it over HTTPS, then remove it


## Phase 7: Deployment — Docker Methods

Implement Docker Compose, Dockerfile, and auto-docker deployment methods.

### Tasks

- [ ] Create systemd service template `systemd.service.j2` (if not done in Phase 5)
- [ ] Extend `pts/appgarden/05_deploy.pct.py`:
  - `deploy_docker_compose(server, name, source, port, url, env/env_file)`:
    - Upload source directory
    - Upload `.env` file (permissions 600)
    - Create and upload systemd unit
    - Deploy Caddy config
    - Register app, enable and start service
  - `deploy_dockerfile(server, name, source, port, container_port, url, env/env_file)`:
    - Upload source to `/srv/appgarden/apps/<name>/source/`
    - Build Docker image on server: `docker build -t appgarden-<name> .`
    - Generate `docker-compose.yml` from template
    - Proceed as docker-compose method
  - `deploy_command(server, name, cmd, port, url, source=None)`:
    - Upload source if provided
    - Create systemd unit with `ExecStart=<cmd>` and `Environment=PORT=<port>`
    - Deploy Caddy config, register, start
- [ ] Create `pts/appgarden/07_auto_docker.pct.py`:
  - `detect_runtime(source_path) -> Runtime` — check for package.json, requirements.txt, etc.
  - `infer_setup_command(runtime) -> str` — e.g., `npm install`, `pip install -r requirements.txt`
  - `generate_dockerfile(runtime, port, cmd, setup_cmd=None) -> str` — render Dockerfile.j2
  - `deploy_auto(server, name, source, port, cmd, url, setup_cmd=None)`:
    - Detect runtime, generate Dockerfile, then proceed as dockerfile method
- [ ] Add CLI flags: `--method`, `--port`, `--cmd`, `--setup-cmd`, `--env`, `--env-file`
- [ ] Handle environment variables: `--env KEY=VALUE` (multiple) and `--env-file <path>`

### Tests

- [ ] `test_deploy.pct.py`: Docker Compose deploy creates correct systemd unit
- [ ] `test_deploy.pct.py`: Dockerfile deploy generates correct docker-compose.yml wrapper
- [ ] `test_deploy.pct.py`: Command deploy creates correct systemd unit with PORT env var
- [ ] `test_auto_docker.pct.py`: Runtime detection for Node.js, Python, Go, Ruby, Rust
- [ ] `test_auto_docker.pct.py`: Setup command inference per runtime
- [ ] `test_auto_docker.pct.py`: Generated Dockerfile has correct structure
- [ ] `test_deploy.pct.py`: Environment file written with 600 permissions
- [ ] `integration/test_deploy_docker.pct.py`: Deploy a simple Docker Compose app, verify it runs and is accessible, then remove it
- [ ] `integration/test_deploy_auto.pct.py`: Deploy a simple Node.js app via auto-docker, verify it runs


## Phase 8: App Lifecycle Management

Implement commands to manage deployed apps.

### Tasks

- [ ] Create `pts/appgarden/06_apps.pct.py`:
  - `list_apps(server) -> list[AppInfo]` — read garden.json, optionally check live status
  - `app_status(server, name) -> AppStatus` — detailed status (systemd state, uptime, URL, method, port)
  - `stop_app(server, name)` — `systemctl stop appgarden-<name>`
  - `start_app(server, name)` — `systemctl start appgarden-<name>`
  - `restart_app(server, name)` — `systemctl restart appgarden-<name>`
  - `app_logs(server, name, follow, lines)` — `journalctl -u appgarden-<name>`
  - `remove_app(server, name, keep_data)`:
    - Stop and disable systemd service
    - Remove systemd unit file
    - Remove Caddy config, reload Caddy
    - Release port
    - Remove from garden.json
    - Remove `/srv/appgarden/apps/<name>/` (unless `keep_data`, then keep `data/` subdirectory)
  - `redeploy_app(server, name)`:
    - If git source: `git pull` on server
    - If local source: re-upload via rsync
    - Rebuild Docker image if applicable
    - Restart service
- [ ] Add CLI commands:
  - `appgarden list [--server]`
  - `appgarden status <name> [--server]`
  - `appgarden stop <name>`, `appgarden start <name>`, `appgarden restart <name>`
  - `appgarden logs <name> [--follow] [--lines N]`
  - `appgarden remove <name> [--keep-data]`
  - `appgarden redeploy <name>`
- [ ] Format `list` output as a rich table (name, URL, method, status)

### Tests

- [ ] `test_apps.pct.py`: `list_apps` parses garden.json correctly
- [ ] `test_apps.pct.py`: `remove_app` cleans up all resources (caddy config, systemd, ports, garden.json)
- [ ] `test_apps.pct.py`: `remove_app` with `keep_data` preserves data directory
- [ ] `test_apps.pct.py`: `app_status` returns correct fields


## Phase 9: Environments

Implement multi-environment deployment via `appgarden.toml`.

### Tasks

- [ ] Create `pts/appgarden/08_environments.pct.py`:
  - `load_project_config(path=".") -> ProjectConfig` — parse `appgarden.toml`
  - `resolve_environment(project_config, env_name) -> DeployConfig` — merge app defaults with environment overrides
  - App name derivation: `<app-name>-<env>` (production can omit suffix)
- [ ] Update deploy CLI to accept an environment name:
  - If argument matches an environment in `appgarden.toml`, use its config
  - If not, treat as app name with explicit flags
  - `appgarden deploy <env-name>` reads `appgarden.toml` from current directory
- [ ] Add flags:
  - `appgarden deploy --all-envs` — deploy all environments
  - `appgarden status <name> --all-envs` — show status of all environments for an app

### Tests

- [ ] `test_environments.pct.py`: Parse valid `appgarden.toml` with multiple environments
- [ ] `test_environments.pct.py`: Environment config merges correctly with app defaults
- [ ] `test_environments.pct.py`: App name derivation (`myapp-staging`, `myapp` for production)
- [ ] `test_environments.pct.py`: Missing environment name raises clear error


## Phase 10: Localhost Tunneling

Implement the `appgarden tunnel` feature.

### Tasks

- [ ] Create `pts/appgarden/09_tunnel.pct.py`:
  - `open_tunnel(server, local_port, url)`:
    - Allocate a remote port
    - Generate and deploy a temporary Caddy config in `/srv/appgarden/caddy/tunnels/`
    - Reload Caddy
    - Record tunnel in `/srv/appgarden/tunnels/active.json`
    - Open SSH reverse tunnel: `ssh -N -R <remote_port>:localhost:<local_port> ...`
    - Print public URL to user
    - Block until Ctrl+C
  - `close_tunnel(server, tunnel_id)`:
    - Remove Caddy config from tunnels directory
    - Reload Caddy
    - Release port
    - Remove from active.json
  - Signal handler for cleanup on Ctrl+C / process exit
  - `list_tunnels(server)` — read active.json
  - `cleanup_stale_tunnels(server)` — detect and remove tunnels whose SSH connections are dead
- [ ] Add CLI commands:
  - `appgarden tunnel <local-port> --url <url> [--server <server>]`
  - `appgarden tunnel list`
  - `appgarden tunnel close <tunnel-id>`

### Tests

- [ ] `test_tunnel.pct.py`: Caddy config generated correctly for tunnel
- [ ] `test_tunnel.pct.py`: Tunnel registered in active.json
- [ ] `test_tunnel.pct.py`: Cleanup removes Caddy config, releases port, updates active.json
- [ ] `test_tunnel.pct.py`: Stale tunnel detection
- [ ] `integration/test_tunnel.pct.py`: Open tunnel, verify HTTPS access via the public URL, close and verify cleanup


## Phase 11: Polish & Documentation

Final polish, error handling, documentation, and end-to-end testing.

### Tasks

- [ ] Improve error messages across all commands (missing config, unreachable server, invalid URL, etc.)
- [ ] Add `--verbose` / `--quiet` global flags for controlling output
- [ ] Add DNS resolution check: warn if a subdomain doesn't resolve to the expected server before deploying
- [ ] Write comprehensive `README.md`:
  - Installation instructions
  - Quick start guide
  - Wildcard DNS setup for Porkbun, Cloudflare, Namecheap
  - Subdomain setup for subdirectory apps
  - All CLI commands with examples
  - Deployment method comparison
  - Environment configuration guide
  - Tunnel usage
  - Subdirectory routing limitations and framework-specific BASE_URL settings
- [ ] `integration/test_e2e.pct.py`: Full end-to-end test — deploy static site, Docker app, check status/list, redeploy, remove, verify cleanup
- [ ] Review all pyinfra operations for idempotency
