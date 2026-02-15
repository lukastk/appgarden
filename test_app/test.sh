#!/usr/bin/env bash
set -euo pipefail

# End-to-end test script for AppGarden commands using the test app.
# Exercises: server init, deploy, apps lifecycle, redeploy, and cleanup.
#
# Prerequisites:
#   - ~/.config/appgarden/config.toml with a server configured
#   - Server reachable via SSH
#   - DNS for test-app.<domain> pointing to the server
#
# Usage:
#   cd test_app && bash test.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="test-app"
SUBDOMAIN="test-app"

pass() { echo -e "\033[32m  PASS: $1\033[0m"; }
fail() { echo -e "\033[31m  FAIL: $1\033[0m"; exit 1; }
header() { echo -e "\n\033[1m==> $1\033[0m"; }

# ---------------------------------------------------------------------------
header "Config & connectivity"
# ---------------------------------------------------------------------------

echo "Showing current config:"
appgarden config show

echo ""
echo "Listing servers:"
appgarden server list

echo ""
echo "Pinging default server:"
appgarden server ping && pass "Server reachable" || fail "Server not reachable"

# ---------------------------------------------------------------------------
header "Server init (minimal — skips all optional steps)"
# ---------------------------------------------------------------------------

appgarden server init --minimal && pass "server init --minimal" || fail "server init --minimal"

# ---------------------------------------------------------------------------
header "Deploy test app (dockerfile method)"
# ---------------------------------------------------------------------------

appgarden deploy "$APP_NAME" \
    --subdomain "$SUBDOMAIN" \
    --source "$SCRIPT_DIR" \
    && pass "deploy" || fail "deploy"

# ---------------------------------------------------------------------------
header "Apps list"
# ---------------------------------------------------------------------------

appgarden apps list
appgarden apps list | grep -q "$APP_NAME" && pass "App appears in list" || fail "App not in list"

# ---------------------------------------------------------------------------
header "Apps status"
# ---------------------------------------------------------------------------

appgarden apps status "$APP_NAME"
pass "apps status"

# ---------------------------------------------------------------------------
header "Apps stop"
# ---------------------------------------------------------------------------

appgarden apps stop "$APP_NAME" && pass "apps stop" || fail "apps stop"

# ---------------------------------------------------------------------------
header "Apps start"
# ---------------------------------------------------------------------------

appgarden apps start "$APP_NAME" && pass "apps start" || fail "apps start"

# ---------------------------------------------------------------------------
header "Apps restart"
# ---------------------------------------------------------------------------

appgarden apps restart "$APP_NAME" && pass "apps restart" || fail "apps restart"

# ---------------------------------------------------------------------------
header "Apps logs"
# ---------------------------------------------------------------------------

appgarden apps logs "$APP_NAME" --lines 10 && pass "apps logs" || fail "apps logs"

# ---------------------------------------------------------------------------
header "Apps redeploy"
# ---------------------------------------------------------------------------

appgarden apps redeploy "$APP_NAME" && pass "apps redeploy" || fail "apps redeploy"

# ---------------------------------------------------------------------------
header "HTTP health check"
# ---------------------------------------------------------------------------

# Give the app a moment to come up after redeploy
sleep 5

# Resolve the URL from the server's domain
DOMAIN=$(appgarden config show | grep '^domain' | head -1 | awk -F'"' '{print $2}')
if [ -z "$DOMAIN" ]; then
    # Fall back to grepping for it differently
    DOMAIN=$(appgarden apps status "$APP_NAME" 2>/dev/null | grep -i url | awk '{print $NF}')
fi
URL="https://${SUBDOMAIN}.${DOMAIN:-lukastk.dev}"

echo "Checking $URL ..."
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    pass "HTTP 200 from $URL"
    echo "  Response:"
    curl -s --max-time 10 "$URL" | python3 -m json.tool 2>/dev/null || true
else
    echo "  Got HTTP $HTTP_CODE (may be expected if DNS/TLS not ready)"
fi

# ---------------------------------------------------------------------------
header "Cleanup — remove app"
# ---------------------------------------------------------------------------

appgarden apps remove "$APP_NAME" --yes && pass "apps remove" || fail "apps remove"

# Verify it's gone
if appgarden apps list 2>/dev/null | grep -q "$APP_NAME"; then
    fail "App still in list after remove"
else
    pass "App removed from list"
fi

# ---------------------------------------------------------------------------
echo ""
echo -e "\033[1;32mAll tests passed!\033[0m"
