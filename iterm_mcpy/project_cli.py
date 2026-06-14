"""`iterm-mcp project` CLI — an agent declares the repo it's working on.

`project set <repo>` emits iTerm's SetUserVar escape (so iTerm sets
``user.mcp_project`` for the current pane) and writes a marker file so the
declaration hook knows to stop asking. `project get` reports the marker.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from core.projects import build_setuservar_escape, PROJECT_VAR, resolve_project

MARKER_DIR = os.path.expanduser("~/.iterm-mcp/projects")


def _key(session_id: Optional[str]) -> str:
    return session_id or os.environ.get("CLAUDE_SESSION_ID", "") or "default"


def cmd_set(repo: str, session_id: Optional[str] = None) -> None:
    """Declare the current session's project and mark it declared."""
    project = resolve_project(repo) or repo  # accept a repo path or any dir
    # 1) Tell iTerm (sets user.mcp_project for THIS pane's session).
    sys.stdout.write(build_setuservar_escape(PROJECT_VAR, project))
    sys.stdout.flush()
    # 2) Persist the marker so the declaration hook stops asking.
    Path(MARKER_DIR).mkdir(parents=True, exist_ok=True)
    (Path(MARKER_DIR) / _key(session_id)).write_text(project + "\n")
    print(f"\nproject set to {project}", file=sys.stderr)


def cmd_get(session_id: Optional[str] = None) -> None:
    marker = Path(MARKER_DIR) / _key(session_id)
    if marker.exists():
        print(marker.read_text().strip())
    else:
        print("project not set for this session")
