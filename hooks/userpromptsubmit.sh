#!/usr/bin/env bash
# ControIDE MC coercion UserPromptSubmit hook.
# Invoked by Claude Code on every UserPromptSubmit event; delegates to
# driver_hook.py. When the MC coercion flag is ON it injects additionalContext
# instructing Claude to end its response with a numbered multiple-choice list
# and resets the per-session reprompt counter.
set -euo pipefail
exec python "$(dirname "$0")/driver_hook.py" userpromptsubmit
