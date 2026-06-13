#!/usr/bin/env bash
# ControIDE MC coercion toggle helper.
#
# Usage:
#   mc_toggle.sh on      — enable always-multiple-choice coercion
#   mc_toggle.sh off     — disable always-multiple-choice coercion
#   mc_toggle.sh status  — print current state (on/off) and flag path
#
# The toggle works by touching / removing the flag file:
#   ~/.iterm-mcp/multiple-choice.on
#
# No daemon restart is needed; hook scripts re-check the file each invocation.
#
# Browser seam: a future POST /api/mc-toggle route could be added near
# /api/answer to let the browser driver flip the flag (touch/rm the file)
# without a shell invocation.  core/dashboard.py is not modified by this PR.

set -euo pipefail

FLAG_FILE="${HOME}/.iterm-mcp/multiple-choice.on"
FLAG_DIR="$(dirname "${FLAG_FILE}")"

cmd="${1:-}"

case "${cmd}" in
  on)
    mkdir -p "${FLAG_DIR}"
    touch "${FLAG_FILE}"
    echo "MC coercion ON  (${FLAG_FILE})"
    ;;
  off)
    rm -f "${FLAG_FILE}"
    echo "MC coercion OFF (${FLAG_FILE} removed)"
    ;;
  status)
    if [ -f "${FLAG_FILE}" ]; then
      echo "MC coercion: ON  (${FLAG_FILE})"
    else
      echo "MC coercion: OFF (${FLAG_FILE} absent)"
    fi
    ;;
  *)
    echo "Usage: mc_toggle.sh on | off | status" >&2
    exit 1
    ;;
esac
