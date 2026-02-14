#!/usr/bin/env bash
set -euo pipefail

# Deploy the test app using global defaults (server=personal, method=dockerfile)
# --subdomain is combined with the server's domain (lukastk.dev) to get test-app.lukastk.dev

appgarden deploy test-app \
    --subdomain test-app \
    --source "$(cd "$(dirname "$0")" && pwd)"
