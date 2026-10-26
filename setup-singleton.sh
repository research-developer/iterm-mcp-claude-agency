#!/usr/bin/env bash
#
# Setup iTerm MCP as a singleton daemon
#
# This script:
# 1. Installs the launchd service for auto-start
# 2. Configures Claude Code to connect via HTTP
# 3. Removes the old stdio-based config

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_URL="http://127.0.0.1:12345/mcp"
# One user-scope entry serves every project; per-project copies just multiply.
SERVER_NAME="iterm"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     iTerm MCP Singleton Daemon Setup                       ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Step 1: Install launchd service
echo -e "${GREEN}[1/4]${NC} Installing launchd service..."
"${SCRIPT_DIR}/install-daemon.sh"
echo ""

# Step 2: Wait for daemon to be ready
echo -e "${GREEN}[2/4]${NC} Waiting for daemon to be ready..."
DAEMON_READY=false
for i in {1..10}; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${DAEMON_URL}" 2>/dev/null) || HTTP_CODE="000"
    if [[ "${HTTP_CODE}" =~ ^(200|405)$ ]]; then
        echo -e "  ${GREEN}✓${NC} Daemon is responding (HTTP ${HTTP_CODE})"
        DAEMON_READY=true
        break
    fi
    sleep 1
done
if [[ "${DAEMON_READY}" != "true" ]]; then
    echo -e "  ${YELLOW}⚠${NC} Daemon may still be starting. Check: tail -f ~/.iterm-mcp/daemon.log"
fi
echo ""

# Step 3: Remove old stdio-based MCP config
echo -e "${GREEN}[3/4]${NC} Checking for existing MCP configurations..."

# Check if there's an old iTerm config
OLD_CONFIGS=$(claude mcp list 2>/dev/null | grep -i "iterm" || echo "")
if [[ -n "${OLD_CONFIGS}" ]]; then
    echo -e "  ${YELLOW}Found existing iTerm configs:${NC}"
    echo "${OLD_CONFIGS}" | sed 's/^/    /'
    echo ""

    # Extract server names that contain 'iterm' (case insensitive)
    # Use while-read to handle names with special characters properly
    claude mcp list 2>/dev/null | grep -ioE "^[^ ]+iterm[^ ]*" | while IFS= read -r name; do
        if [[ -n "${name}" ]]; then
            echo -e "  Removing old config: ${name}"
            claude mcp remove "${name}" --yes 2>/dev/null || true
        fi
    done
else
    echo -e "  ${GREEN}✓${NC} No existing iTerm configs found"
fi
echo ""

# Step 4: Add HTTP-based config
echo -e "${GREEN}[4/4]${NC} Adding HTTP-based MCP config..."
claude mcp add --scope user --transport http "${SERVER_NAME}" "${DAEMON_URL}" 2>/dev/null || {
    echo -e "  ${YELLOW}⚠${NC} Config may already exist or command failed"
    echo "  Try manually: claude mcp add --scope user --transport http ${SERVER_NAME} ${DAEMON_URL}"
}
echo ""

# Summary
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Daemon status:"
if launchctl list com.iterm-mcp.daemon &>/dev/null; then
    echo "  Service: loaded"
    PID=$(launchctl list com.iterm-mcp.daemon 2>/dev/null | grep PID | awk '{print $2}')
    [[ -n "${PID}" && "${PID}" != "-" ]] && echo "  PID: ${PID}"
else
    echo "  Service: not loaded"
fi
echo ""
echo "Current MCP config:"
claude mcp list 2>/dev/null | head -10
echo ""
echo -e "${GREEN}Benefits of singleton mode:${NC}"
echo "  • Single process for ALL Claude Code instances"
echo "  • ~90% less memory usage (100MB vs 1.5GB for 15 instances)"
echo "  • Single iTerm2 connection"
echo "  • Shared session cache"
echo ""
echo -e "${YELLOW}To revert to stdio mode:${NC}"
echo "  1. Stop the daemon:"
echo "     launchctl bootout gui/\$(id -u) ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist"
echo "  2. Remove HTTP config:"
echo "     claude mcp remove ${SERVER_NAME} -s user"
echo "  3. Re-add with stdio transport (spawns a shim per session):"
echo "     claude mcp add --scope user ${SERVER_NAME} -- ${SCRIPT_DIR}/.venv/bin/python -m iterm_mcpy"
echo ""
echo "Full guide: ${SCRIPT_DIR}/docs/RUNNING.md"
