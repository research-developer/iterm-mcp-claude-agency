#!/usr/bin/env bash
# ControIDE Phase-0 Stop hook.
# Invoked by Claude Code on every Stop event; delegates to driver_hook.py.
set -euo pipefail
exec python "$(dirname "$0")/driver_hook.py" stop
