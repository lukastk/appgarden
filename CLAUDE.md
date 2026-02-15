# AppGarden - CLAUDE.md

## Project Overview

AppGarden is a Python CLI tool for deploying web applications to remote servers. It uses `pyinfra` for remote operations over SSH and `Caddy` as a reverse proxy with automatic HTTPS. See `PROJECT_SPEC.md` for the full specification and `PROJECT_PLAN.md` for the implementation plan.

## nblite Project — Critical Rules

This is a **nblite** project. Source code lives in notebooks and is exported to Python modules.

1. **NEVER edit files under `src/`** — they are auto-generated and will be overwritten by `nbl export`
2. **Edit `.pct.py` files in `pts/`** — these are the source of truth (plaintext percent-format notebooks)
3. **After editing `.pct.py` files**, run: `nbl export --reverse && nbl export`
4. **Static assets** in `src/appgarden/templates/` are the exception — these are Jinja2 templates, NOT auto-generated, and can be edited directly
5. **Read `NBLITE_INSTRUCTIONS.md`** for full details on directives, workflows, and conventions

## Project Structure

```
appgarden/
├── nblite.toml                    # nblite export pipeline config
├── pyproject.toml                 # Project metadata, dependencies, CLI entry point
├── CLAUDE.md                      # This file
├── PROJECT_SPEC.md                # Full project specification
├── PROJECT_PLAN.md                # Phased implementation plan
├── NBLITE_INSTRUCTIONS.md         # nblite usage guide
├── nbs/                           # Jupyter notebooks (auto-synced from pts/)
│   ├── appgarden/                 # Main package notebooks
│   └── tests/                     # Test notebooks
├── pts/                           # Percent-format notebooks (EDIT THESE)
│   ├── appgarden/                 # Main package source
│   │   ├── 00_config.pct.py       # Local config management
│   │   ├── 01_remote.pct.py       # Remote state read/write via pyinfra
│   │   ├── 02_ports.pct.py        # Port allocation
│   │   ├── 03_routing.pct.py      # Caddy config generation
│   │   ├── 04_server.pct.py       # Server management & init
│   │   ├── 05_deploy.pct.py       # Deployment logic
│   │   ├── 06_apps.pct.py         # App lifecycle (start/stop/status/remove)
│   │   ├── 07_auto_docker.pct.py  # Auto Dockerfile generation
│   │   ├── 08_environments.pct.py # Environment handling
│   │   ├── 09_tunnel.pct.py       # Localhost tunneling
│   │   └── 10_cli.pct.py          # Typer CLI entry point
│   └── tests/                     # Test notebooks
│       ├── test_apps.pct.py       # Unit tests
│       ├── test_auto_docker.pct.py
│       ├── test_config.pct.py
│       ├── test_deploy.pct.py
│       ├── test_environments.pct.py
│       ├── test_ports.pct.py
│       ├── test_remote.pct.py
│       ├── test_routing.pct.py
│       ├── test_server.pct.py
│       ├── test_tunnel.pct.py
│       ├── test_validate.pct.py
│       └── integration/           # Integration tests (require .env + hcloud)
│           ├── conftest.pct.py    # Server provisioning fixtures
│           └── test_server_init.pct.py
├── src/                           # AUTO-GENERATED (do not edit)
│   ├── appgarden/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── remote.py
│   │   ├── ...
│   │   └── templates/             # Static Jinja2 templates (NOT auto-generated)
│   │       ├── systemd.service.j2
│   │       ├── docker-compose.yml.j2
│   │       ├── Dockerfile.j2
│   │       ├── Caddyfile.subdomain.j2
│   │       ├── Caddyfile.subdirectory.j2
│   │       └── Caddyfile.static.j2
│   └── tests/
├── test_app/                      # Sample project with appgarden.toml
├── SKILL.md                       # Agent skill guide for using appgarden
└── README.md
```

## Commands

```bash
# Export notebooks to Python modules
nbl export

# Sync .pct.py changes back to .ipynb, then export to modules
nbl export --reverse && nbl export

# Run unit tests (no server needed)
uv run pytest src/tests/ -v

# Run integration tests (provisions a Hetzner Cloud server, requires .env)
uv run pytest src/tests/ -v -m integration

# Run all tests
uv run pytest src/tests/ -v -m ""

# Run the CLI (during development)
uv run appgarden --help

# Create a new notebook
nbl new pts/appgarden/my_module.pct.py
```

## Testing

**Unit tests** (`pts/tests/`): Run by default with `pytest`. Test pure logic — no remote operations.

**Integration tests** (`pts/tests/integration/`): Run only with `pytest -m integration`. These provision a real Hetzner Cloud server via `hcloud`, run deployments against it, and tear it down afterwards.

Integration tests require a `.env` file in the repo root (see `.env.sample`). The `.env` file is gitignored.

## Notebook Conventions

- Each notebook starts with `#|default_exp module_name` to set the export target
- Use `#|export` on cells that should be included in the generated module
- Use `#|hide` on setup cells (like `nbl_export()` calls)
- Use `#|exporti` for internal functions (excluded from `__all__`)
- Non-exported cells serve as documentation and examples
- Markdown cells (`# %% [markdown]`) provide documentation between code

## Key Dependencies

- `typer` — CLI framework
- `pyinfra` — Remote server operations over SSH
- `jinja2` — Template rendering for Caddy/systemd/Docker configs
- `rich` — Terminal output (tables, progress bars, colors)
- `tomli` — TOML parsing (for config files)

## Architecture Notes

- **Agentless**: No daemon on the server. All operations run locally via pyinfra over SSH.
- **Remote state**: App registry and config stored on server at `/srv/appgarden/`
- **Caddy**: Each app gets an explicit `.caddy` file; Caddy obtains TLS certs on reload.
- **Deployment methods**: `static`, `command`, `dockerfile`, `docker-compose`, `auto`
- **Templates**: Jinja2 templates in `src/appgarden/templates/` generate systemd units, Caddyfiles, Dockerfiles, and docker-compose files.
