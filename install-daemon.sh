#!/usr/bin/env bash
#
# Install iTerm MCP daemon as a launchd service
#
# This installs the daemon to run automatically on login.
# The daemon provides a singleton MCP server for all Claude Code instances.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.iterm-mcp.daemon.plist"
PLIST_TEMPLATE="${SCRIPT_DIR}/${PLIST_NAME}.template"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
LOG_DIR="${HOME}/.iterm-mcp"

# Auto-detect Python interpreter. Prefer the repo's dedicated venv so
# unrelated installs into a shared env can't break the daemon.
if [[ -n "${ITERM_MCP_PYTHON}" ]]; then
    PYTHON_PATH="${ITERM_MCP_PYTHON}"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    PYTHON_PATH="${SCRIPT_DIR}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_PATH="$(command -v python3)"
else
    PYTHON_PATH="$(command -v python || echo python3)"
fi
PYTHON_BIN_DIR="$(dirname "${PYTHON_PATH}")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Installing iTerm MCP daemon...${NC}"

# Verify Python interpreter exists
if [[ ! -x "${PYTHON_PATH}" ]]; then
    echo -e "${RED}Error: Python interpreter not found at ${PYTHON_PATH}${NC}"
    echo "Set ITERM_MCP_PYTHON environment variable to specify the Python path."
    exit 1
fi

# Refuse to install an interpreter that can't load the server; launchd
# would otherwise crash-loop it forever.
PREFLIGHT_LOG="$(mktemp)"
if ! (cd "${SCRIPT_DIR}" && "${PYTHON_PATH}" -c "import iterm_mcpy.fastmcp_server") 2>"${PREFLIGHT_LOG}"; then
    echo -e "${RED}Error: ${PYTHON_PATH} cannot import the server:${NC}"
    tail -3 "${PREFLIGHT_LOG}"
    rm -f "${PREFLIGHT_LOG}"
    echo "Create the venv first:  python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi
rm -f "${PREFLIGHT_LOG}"

# Create log directory
mkdir -p "${LOG_DIR}"

# Stop existing service if running
if launchctl list 2>/dev/null | grep -q "com.iterm-mcp.daemon"; then
    echo -e "${YELLOW}Stopping existing service...${NC}"
    launchctl unload "${PLIST_DST}" 2>/dev/null || true
fi

# Generate plist from template with user-specific paths
echo "Generating launchd plist..."
if [[ -f "${PLIST_TEMPLATE}" ]]; then
    sed -e "s|__PYTHON_PATH__|${PYTHON_PATH}|g" \
        -e "s|__PYTHON_BIN_DIR__|${PYTHON_BIN_DIR}|g" \
        -e "s|__SCRIPT_DIR__|${SCRIPT_DIR}|g" \
        -e "s|__LOG_DIR__|${LOG_DIR}|g" \
        "${PLIST_TEMPLATE}" > "${PLIST_DST}"
else
    echo -e "${RED}Error: Template file not found: ${PLIST_TEMPLATE}${NC}"
    exit 1
fi

# Load the service
echo "Loading service..."
launchctl load "${PLIST_DST}"

# Check status
sleep 2
if launchctl list 2>/dev/null | grep -q "com.iterm-mcp.daemon"; then
    echo -e "${GREEN}Service installed and running!${NC}"
    echo ""
    echo "Python: ${PYTHON_PATH}"
    echo "Daemon endpoint: http://127.0.0.1:12345/mcp"
    echo "Logs: ${LOG_DIR}/daemon.log"
    echo ""
    echo "To check status:  ${PYTHON_BIN_DIR}/iterm-mcp status"
    echo "To restart:       launchctl kickstart -k gui/\$(id -u)/com.iterm-mcp.daemon"
    echo "To stop:          launchctl unload ${PLIST_DST}"
    echo "To start:         launchctl load ${PLIST_DST}"
    echo "To uninstall:     launchctl unload ${PLIST_DST} && rm ${PLIST_DST}"
    echo "Docs:             ${SCRIPT_DIR}/docs/RUNNING.md"
else
    echo -e "${YELLOW}Service installed but may not be running. Check logs:${NC}"
    echo "  tail -f ${LOG_DIR}/daemon.log"
fi
