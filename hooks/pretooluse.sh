#!/usr/bin/env bash
# ControIDE Phase-0 PreToolUse hook.
# Invoked by Claude Code before every tool call; delegates to driver_hook.py.
set -euo pipefail
exec python "$(dirname "$0")/driver_hook.py" pretooluse
