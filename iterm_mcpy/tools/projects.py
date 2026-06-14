"""`projects` tool — list iTerm sessions grouped by their project tag."""
from collections import defaultdict
from typing import Any

from iterm_mcpy.responses import err_envelope, ok_envelope
from core.projects import get_session_project, project_label

_OPTIONS = {
    "tool": "projects", "kind": "action",
    "ops": {"GET": "list sessions grouped by project", "OPTIONS": "this schema"},
}


async def projects(ctx, op: str = "GET", **kwargs) -> dict[str, Any]:
    """List iTerm sessions grouped by their project tag.

    Args:
        ctx: FastMCP context carrying lifespan state.
        op: HTTP verb or alias. Supported: GET (default), OPTIONS.

    Returns:
        Envelope dict with ``data`` list of project groups or OPTIONS schema.
    """
    if str(op).upper() == "OPTIONS":
        return ok_envelope(method="OPTIONS", data=_OPTIONS)

    lifespan = ctx.request_context.lifespan_context
    terminal = lifespan["terminal"]
    logger = lifespan.get("logger")

    try:
        groups: dict[str, list[str]] = defaultdict(list)
        for session in list(terminal.sessions.values()):
            proj = await get_session_project(
                getattr(terminal, "connection", None), session.id
            )
            groups[proj or "(unassigned)"].append(session.id)

        data = [
            {
                "project": p,
                "label": project_label(p) if p != "(unassigned)" else p,
                "sessions": sids,
                "count": len(sids),
            }
            for p, sids in sorted(groups.items())
        ]
        return ok_envelope(method="GET", data=data)

    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.error("projects tool error: %s", exc)
        return err_envelope(method="GET", error=str(exc))


def register(mcp):
    """Register the projects action tool."""
    mcp.tool(name="projects")(projects)
