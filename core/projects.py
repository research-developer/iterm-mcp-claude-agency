"""Project identity for iTerm sessions.

The authoritative key is the iTerm2 session variable ``user.mcp_project``.
This module derives a stable project id from a CWD (the git repo root) and
builds the iTerm ``SetUserVar`` escape an agent uses to declare its project.

Env-assumption findings (Task 1 of the plan):
    SetUserVar form: ESC ] 1337 ; SetUserVar=<name>=<base64(value)> BEL
    (record any correction discovered during live verification here)
"""

import base64
import os
import subprocess
from typing import Optional

from core.iterm_path_monitor import (
    get_user_variable,
    set_user_variable,
    get_session_path,
)

#: The session variable that holds a session's project (absolute path).
PROJECT_VAR = "mcp_project"  # stored by iTerm as ``user.mcp_project``


def resolve_project(cwd: Optional[str]) -> Optional[str]:
    """Return the project id for a working directory.

    The project is the git repo root of ``cwd`` (so subdir navigation within
    a repo stays the same project). Non-git dirs fall back to ``cwd`` itself.
    Returns ``None`` for an empty/None cwd.
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


def project_label(project_id: Optional[str]) -> Optional[str]:
    """Human-readable label for a project id (its basename)."""
    if not project_id:
        return None
    return os.path.basename(project_id.rstrip("/")) or project_id


def build_setuservar_escape(name: str, value: str) -> str:
    """Build iTerm2's OSC 1337 SetUserVar escape (value base64-encoded)."""
    b64 = base64.b64encode(value.encode()).decode()
    return f"\033]1337;SetUserVar={name}={b64}\007"


async def get_session_project(connection, session_id: str) -> Optional[str]:
    """Return a session's project, inferring + pinning it once if unset.

    Sticky / first-observation-wins: if ``user.mcp_project`` is already set
    (declared by the agent or pinned earlier), it is returned unchanged and
    never overwritten. Otherwise the project is inferred from the session's
    current CWD (git repo root) and pinned by setting ``user.mcp_project``
    exactly once. Returns ``None`` if the project is unset and no CWD is
    available yet (nothing is pinned in that case).
    """
    existing = await get_user_variable(connection, session_id, PROJECT_VAR)
    if existing:
        return existing
    cwd = await get_session_path(connection, session_id)
    project = resolve_project(cwd)
    if not project:
        return None
    await set_user_variable(connection, session_id, PROJECT_VAR, project)
    return project
