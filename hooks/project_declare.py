"""UserPromptSubmit hook: nudge an agent to declare its project (once)."""
import json
import os
import re
import sys
from pathlib import Path

MARKER_DIR = os.path.expanduser("~/.iterm-mcp/projects")
MAX_PROMPTS = 2

_INSTRUCTION = (
    "PROJECT SETUP: Your iTerm session is not yet tagged with the repo/project "
    "you are working on. Run this once, with the absolute path of the repo you "
    "are actually working on (NOT necessarily your current directory):\n"
    "  iterm-mcp project set <repo-path> --session-id {sid}\n"
    "This lets the system group your session under the right project."
)


def _safe(session_id: str) -> str:
    """Sanitize a session id for path keying (mirrors project_cli._key)."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", session_id or "default") or "default"


def _marker(sid: str) -> Path:
    return Path(MARKER_DIR) / _safe(sid)


def _asked_path(sid: str) -> Path:
    return Path(MARKER_DIR) / f"{_safe(sid)}.asked"


def _read_asked(sid: str) -> int:
    try:
        return int(_asked_path(sid).read_text().strip())
    except (OSError, ValueError):
        return 0


def decide(payload: dict) -> dict:
    """Return the UserPromptSubmit hook JSON for this turn."""
    base = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}
    sid = payload.get("session_id") or "default"
    if _marker(sid).exists():
        return base  # declared -> no-op
    asked = _read_asked(sid)
    if asked >= MAX_PROMPTS:
        return base  # gave up nagging; server-side inference will pin a fallback
    try:
        Path(MARKER_DIR).mkdir(parents=True, exist_ok=True)
        _asked_path(sid).write_text(str(asked + 1))
    except OSError:
        pass
    base["hookSpecificOutput"]["additionalContext"] = _INSTRUCTION.format(sid=sid)
    return base


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    json.dump(decide(payload), sys.stdout)


if __name__ == "__main__":
    main()
