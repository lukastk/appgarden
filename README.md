# AppGarden

Deploy web applications to remote servers.

AppGarden manages a "garden" of web applications on remote servers. It handles deployment, routing, TLS certificates, and lifecycle management — all without requiring a persistent daemon on the server.

## Installation

```bash
pip install appgarden
```

## Quick Start

```bash
# Add a server
appgarden server add myserver \
  --host 203.0.113.10 \
  --ssh-user root \
  --ssh-key ~/.ssh/id_rsa \
  --domain apps.example.com

# Initialise the server (installs Docker, Caddy, etc.)
appgarden server init myserver

# Deploy an app
appgarden deploy myapp \
  --method auto \
  --source ./my-project/ \
  --port 3000 \
  --cmd "npm start" \
  --url myapp.apps.example.com
```

## DNS Setup

For subdomain-based routing (recommended), configure a **wildcard DNS record**:

```
*.apps.example.com.    A    <server-ip>
apps.example.com.      A    <server-ip>
```

### Porkbun

1. Go to Domain Management → DNS Records
2. Add record: Type `A`, Host `*.apps`, Answer `<server-ip>`
3. Add record: Type `A`, Host `apps`, Answer `<server-ip>`

### Cloudflare

1. Go to DNS → Records
2. Add record: Type `A`, Name `*.apps`, IPv4 `<server-ip>`, Proxy status: DNS only
3. Add record: Type `A`, Name `apps`, IPv4 `<server-ip>`

This way, any new subdomain deployed by AppGarden will automatically resolve to your server, and Caddy will obtain TLS certificates on demand.
