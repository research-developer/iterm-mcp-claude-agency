"""Test window tracking and hygiene for iTerm2 integration tests.

Provides per-run tagging and safe teardown logic so that integration tests
never close windows opened by the user or a concurrent test run.

Tag format: ``MCP-TEST·<pid>-<uuid8>``

Usage example::

    from core.test_window_tracker import (
        make_run_tag, mark_session, close_tagged_sessions,
        ensure_test_profile,
    )

    tag = make_run_tag()

    # Once at test-suite startup — writes the stable MCP-TEST dynamic profile
    # (idempotent; skipped if the file already exists with the right content):
    ensure_test_profile()

    # After creating a session:
    await mark_session(raw_iterm2_session, tag)

    # In teardown:
    closed = await close_tagged_sessions(connection, tag)

Profile strategy
----------------
``ensure_test_profile()`` writes a stable ``MCP-TEST`` iTerm2 Dynamic Profile
to ``~/Library/Application Support/iTerm2/DynamicProfiles/iterm-mcp-test-profile.json``
exactly once.  The profile has a distinctive orange badge and background tint
so test windows are immediately eyeball-trackable.

Because iTerm2 may not reload the profile synchronously after the file is
written, ``create_window(profile="MCP-TEST")`` falls back to the default
profile if ``MCP-TEST`` is not loaded yet.  That is acceptable — the
``user.mcp_test_run`` variable set unconditionally after window creation
remains the functional teardown key, so every test window is closeable even
if it missed the profile.
"""

import json
import os
import re
import uuid
import logging
from pathlib import Path
from typing import Optional

import iterm2

log = logging.getLogger("iterm-mcp.test-tracker")

# Stable prefix; orphan sweep matches profile names starting with "MCP-TEST"
# (covering the stable MCP-TEST profile and any MCP-TEST·<run> variants).
# Production profiles start with "MCP Agent" or "MCP Team:" — never this prefix.
TAG_PREFIX = "MCP-TEST·"  # middle-dot U+00B7

# ---------------------------------------------------------------------------
# Stable visible test profile
# ---------------------------------------------------------------------------

#: Exact name of the stable iTerm2 Dynamic Profile used for test windows.
TEST_PROFILE_NAME = "MCP-TEST"

#: Stable GUID for the MCP-TEST dynamic profile.  Never changes so iTerm2
#: can track it across restarts without creating orphan entries.
_TEST_PROFILE_GUID = "E7A1C3F0-9B2D-4E8A-B5C6-D1F234567890"

#: Path where the single-file dynamic profile is written.
_DYNAMIC_PROFILES_DIR = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles"
_TEST_PROFILE_FILE = _DYNAMIC_PROFILES_DIR / "iterm-mcp-test-profile.json"

#: The canonical profile dict.  Written once and never overwritten if already
#: present, avoiding per-run churn that would trigger iTerm2 reload races.
_TEST_PROFILE_DATA: dict = {
    "Profiles": [
        {
            "Name": TEST_PROFILE_NAME,
            "Guid": _TEST_PROFILE_GUID,
            "Dynamic Profile Parent Name": "Default",
            # Vivid amber/orange tab colour — stands out from production sessions.
            "Custom Tab Color": True,
            "Tab Color": {
                "Red Component": 0.85,
                "Green Component": 0.45,
                "Blue Component": 0.05,
                "Color Space": "sRGB",
            },
            # Human-readable badge so the window label reads "MCP-TEST" even
            # when the tab is too narrow to show the profile name.
            "Badge Text": "MCP-TEST",
            "Tags": ["mcp", "test"],
        }
    ]
}


def ensure_test_profile(
    profiles_dir: Optional[Path] = None,
) -> Path:
    """Write the stable ``MCP-TEST`` iTerm2 Dynamic Profile if not present.

    Idempotent: if the file already exists it is NOT overwritten, so there is
    no per-run reload race in iTerm2.  Create the DynamicProfiles directory
    if it does not exist (mirrors what ``ProfileManager`` does).

    Args:
        profiles_dir: Override the DynamicProfiles directory (used in tests to
            point at a temp dir instead of the real user library).

    Returns:
        Path to the profile file (written or pre-existing).
    """
    target_dir = profiles_dir or _DYNAMIC_PROFILES_DIR
    target_file = target_dir / "iterm-mcp-test-profile.json"

    try:
        target_dir.mkdir(parents=True, exist_ok=True)

        if target_file.exists():
            log.debug("ensure_test_profile: profile file already exists at %s", target_file)
            return target_file

        target_file.write_text(json.dumps(_TEST_PROFILE_DATA, indent=2))
        log.info(
            "ensure_test_profile: wrote MCP-TEST dynamic profile to %s "
            "(iTerm2 will load it shortly; first window may fall back to default profile)",
            target_file,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "ensure_test_profile: could not write profile file to %s: %s "
            "(test windows will still open and be tagged for teardown)",
            target_file,
            exc,
        )

    return target_file


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
                # Match both the stable "MCP-TEST" profile name AND per-run
                # "MCP-TEST·…" variants.  Production profiles ("MCP Agent",
                # "MCP Team: …") never start with "MCP-TEST".
                if not should_close and prefix_sweep:
                    try:
                        prof = await session.async_get_profile()
                        if prof.name.startswith("MCP-TEST"):
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
