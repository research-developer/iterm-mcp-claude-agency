"""Test window tracking and hygiene for iTerm2 integration tests.

Provides per-run tagging and safe teardown logic so that integration tests
never close windows opened by the user or a concurrent test run.

Tag format: ``MCP-TEST·<pid>-<uuid8>``

Usage example::

    from core.test_window_tracker import make_run_tag, mark_session, close_tagged_sessions

    tag = make_run_tag()

    # After creating a session:
    await mark_session(raw_iterm2_session, tag)

    # In teardown:
    closed = await close_tagged_sessions(connection, tag)
"""

import os
import re
import uuid
import logging
from typing import Optional

import iterm2

log = logging.getLogger("iterm-mcp.test-tracker")

# Stable prefix that identifies any test-opened session by profile name.
# Production profiles start with "MCP Agent" or "MCP Team:" — never this prefix.
TAG_PREFIX = "MCP-TEST·"  # middle-dot U+00B7


def make_run_tag() -> str:
    """Mint a unique per-run tag string.

    The tag encodes the current PID (to differentiate concurrent processes)
    and 8 hex chars of a UUID4 (to handle PID reuse).

    Returns:
        A string like ``MCP-TEST·12345-a1b2c3d4``.
    """
    return f"{TAG_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def mark_session(session: iterm2.Session, tag: str) -> None:
    """Set the ``user.mcp_test_run`` variable on a session.

    The variable must begin with ``user.`` — the iTerm2 API enforces this.
    Errors are logged but not re-raised; a failure to tag is non-fatal at
    mark time (the session just won't be closed at teardown).

    Args:
        session: The raw ``iterm2.Session`` object (not an ``ItermSession``
            wrapper) returned by ``window.tabs[0].sessions[0]`` etc.
        tag: The run tag produced by :func:`make_run_tag`.
    """
    try:
        await session.async_set_variable("user.mcp_test_run", tag)
        log.debug("Tagged session %s with %s", session.session_id, tag)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Failed to tag session %s: %s", getattr(session, "session_id", "?"), exc
        )


async def close_tagged_sessions(
    connection: iterm2.Connection,
    tag: str,
    *,
    prefix_sweep: bool = False,
) -> int:
    """Enumerate every iTerm2 session and close those that belong to this run.

    A session is closed if **either** of the following is true:

    1. Its ``user.mcp_test_run`` variable equals *tag* exactly.
    2. ``prefix_sweep=True`` AND its profile name starts with
       :data:`TAG_PREFIX` (catches orphans from crashed prior runs).

    Sessions that raise an exception during variable/profile reads are
    **skipped** (not fatal).  Sessions whose marker does not match are
    **never** touched.

    Args:
        connection: An active ``iterm2.Connection``.
        tag: The run tag to match against.
        prefix_sweep: If ``True``, also close sessions whose profile name
            starts with :data:`TAG_PREFIX` regardless of their variable
            value.  Use this once at the start of a test run to clean up
            orphans from previously crashed runs.

    Returns:
        The number of sessions successfully closed.
    """
    try:
        app = await iterm2.async_get_app(connection)
    except Exception as exc:  # noqa: BLE001
        log.error("close_tagged_sessions: could not get app: %s", exc)
        return 0

    closed = 0

    for window in app.windows:
        for tab in window.tabs:
            for session in tab.sessions:
                should_close = False

                # --- primary check: user variable matches exactly ---
                try:
                    var = await session.async_get_variable("user.mcp_test_run")
                    if var == tag:
                        should_close = True
                except Exception as exc:  # noqa: BLE001
                    log.debug(
                        "Could not read user.mcp_test_run from %s: %s",
                        getattr(session, "session_id", "?"),
                        exc,
                    )

                # --- secondary check: profile name prefix (orphan sweep) ---
                if not should_close and prefix_sweep:
                    try:
                        prof = await session.async_get_profile()
                        if prof.name.startswith(TAG_PREFIX):
                            should_close = True
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "Could not read profile from %s: %s",
                            getattr(session, "session_id", "?"),
                            exc,
                        )

                if should_close:
                    try:
                        await session.async_close(force=True)
                        closed += 1
                        log.debug(
                            "Closed test session %s",
                            getattr(session, "session_id", "?"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Already closed by another path — not an error.
                        log.debug(
                            "Could not close session %s (may already be gone): %s",
                            getattr(session, "session_id", "?"),
                            exc,
                        )

    log.info("close_tagged_sessions: closed %d session(s) for tag=%s", closed, tag)
    return closed


# ---------------------------------------------------------------------------
# Internal helpers for tests that need to inspect the tag format
# ---------------------------------------------------------------------------

_TAG_PATTERN = re.compile(
    r"^MCP-TEST·(\d+)-([0-9a-f]{8})$"
)


def _parse_tag(tag: str) -> Optional[tuple]:
    """Parse a tag string into (pid_str, uuid8_str), or None if invalid.

    Args:
        tag: Tag string to parse.

    Returns:
        ``(pid_str, uuid8_str)`` if the tag is well-formed, else ``None``.
    """
    m = _TAG_PATTERN.match(tag)
    if m:
        return m.group(1), m.group(2)
    return None
