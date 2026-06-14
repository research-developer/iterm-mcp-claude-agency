# CWD-Based Project Segmentation — Implementation Plan (v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every iTerm2 session with a stable `project` (declared by the agent, else inferred from its git repo root) so sessions can be listed, filtered, and targeted by project — robustly, even when an agent's CWD lies or resets every turn.

**Architecture:** The authoritative key is the iTerm2 session variable **`user.mcp_project`**. It is set *declared-first* (an agent runs `iterm-mcp project set <repo>`, which emits iTerm's `SetUserVar` escape) and *sticky*: once set it is never overwritten. For undeclared sessions, the project is **lazily inferred on demand** (git repo root of the session's current CWD) and pinned once — no background monitor (the repo's `PathMonitor` is dormant and carries unrelated side effects, so we don't wake it). A one-time `UserPromptSubmit` hook nudges undeclared agents to declare. Query/targeting is surfaced through the existing `sessions` tool plus a new `projects` listing.

**Tech Stack:** Python 3 (stdlib `subprocess`/`base64`), `unittest` + `unittest.mock`, the iTerm2 Python API (`async_get_variable`/`async_set_variable`), the existing FastMCP tool + `iterm-mcp` argparse CLI patterns.

**Spec:** `docs/superpowers/specs/2026-06-13-project-segmentation-design.md`

---

## Deviations from the spec (decided at plan time, from the integration map)

1. **No background path monitor.** `core/iterm_path_monitor.py:PathMonitor` is never constructed or started anywhere (grep confirms zero call sites). Waking it would also activate team-assignment/styling/session-id side effects and run a live iTerm connection loop. v1 instead does **on-demand inference + sticky pin** in `core/projects.py`, achieving identical first-observation-wins semantics. (Background monitoring can be revisited in the manager phase.)
2. **No `Agent` dataclass change in v1.** The project lives on the iTerm session var (`user.mcp_project`), read per-session at query time. Adding a first-class `Agent.project` field (and the `save_state`/`load_state` persistence fixups the map flags) is deferred to the manager phase, where the bus `project:` fan-out needs it.
3. **Future seams are documented, not built:** `project:` bus addressing, `Agent.project`, `project_summary()`, and per-project visual profiles. Exact insertion points are noted so they slot in later.

## File structure

| File | Responsibility |
|---|---|
| `core/projects.py` (new) | `resolve_project(cwd)` (git-root) + `project_label`; `build_setuservar_escape`; async `pin_session_project` / `get_session_project` (read `user.mcp_project`, infer+pin if unset); a documented `project_summary` seam. |
| `iterm_mcpy/project_cli.py` (new) | `project set <repo>` / `project get` handlers (emit escape, write/read the per-session declared marker). |
| `iterm_mcpy/main.py` (modify) | Add the `project` subcommand group + dispatch branch. |
| `hooks/project_declare.py` (new) + `hooks/project_declare.sh` (new) | `UserPromptSubmit` hook: nudge an undeclared agent to run `iterm-mcp project set`, ≤ N times, then stop. |
| `iterm_mcpy/tools/sessions.py` (modify) | Add a `project=` filter to the session list path. |
| `core/models.py` (modify) | Add `project: Optional[str]` to `SessionInfo`. |
| `iterm_mcpy/tools/projects.py` (new) + `tools/__init__.py` (modify) | New `projects` MCP tool: list sessions grouped by project. |
| `docs/examples/hooks-settings.json` (modify) | Wire the `UserPromptSubmit` project hook (example only). |
| `tests/test_projects.py`, `tests/test_project_cli.py`, `tests/test_project_hook.py`, `tests/test_projects_tool.py` (new); `tests/test_sessions.py` (extend) | Headless tests (mocked iTerm — **no live windows, no full-suite runs**). |

**Test-safety rule (mandatory):** never run the full suite or the live-iTerm2 modules. Verify each task with its own module only, e.g. `python -m unittest tests.test_projects -v`.

---

### Task 1: Verify two environment assumptions (spike — no code)

**Why:** Two design points depend on facts the plan can't assume. Resolve them before building the declaration/hook tasks.

- [ ] **Step 1: Confirm the `SetUserVar` escape sets `user.mcp_project`.** In a real iTerm2 pane (a non-test pane you own), run:

```bash
printf '\033]1337;SetUserVar=mcp_project=%s\007' "$(printf '%s' '/tmp/demo' | base64)"
```

Then confirm with a tiny script (iterm2 lib present):

```bash
/opt/anaconda3/bin/python - <<'PY'
import iterm2
async def main(conn):
    app = await iterm2.async_get_app(conn)
    w = app.current_terminal_window
    s = w.current_tab.current_session
    print("mcp_project =", repr(await s.async_get_variable("user.mcp_project")))
iterm2.run_until_complete(main)
PY
```
Expected: `mcp_project = '/tmp/demo'`. If the framing differs (e.g. no base64, or value not decoded), record the exact working form — Tasks 3/4 use it.

- [ ] **Step 2: Determine whether `CLAUDE_SESSION_ID` is available to the agent's shell.** From inside a Claude Code agent's Bash, run `echo "${CLAUDE_SESSION_ID:-UNSET}"`. Record the result:
  - If a session id is printed → the hook's "already declared?" check keys the marker file by `$CLAUDE_SESSION_ID` (precise; stops asking the instant the agent declares).
  - If `UNSET` → the hook uses the session id it already receives on **stdin** for its marker, and `project set` writes the marker keyed by an id passed via the hook's instruction. Either way Task 4/5 below cover both branches; just note which path is live.

- [ ] **Step 3: Record findings** as a short comment block at the top of `core/projects.py` (created in Task 2) so downstream tasks have the confirmed escape form. No commit yet (folded into Task 2's commit).

---

### Task 2: `core/projects.py` — pure project resolver + escape builder

**Files:**
- Create: `core/projects.py`
- Test: `tests/test_projects.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the pure project resolver + SetUserVar escape builder."""
import base64
import unittest
from unittest import mock

from core import projects


class TestResolveProject(unittest.TestCase):
    def test_git_repo_returns_toplevel(self):
        with mock.patch("core.projects.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="/Users/me/repoA\n")
            self.assertEqual(projects.resolve_project("/Users/me/repoA/sub/dir"), "/Users/me/repoA")
        # called git -C <cwd> rev-parse --show-toplevel
        args = run.call_args[0][0]
        self.assertIn("rev-parse", args)
        self.assertIn("--show-toplevel", args)
        self.assertIn("/Users/me/repoA/sub/dir", args)

    def test_non_git_falls_back_to_cwd(self):
        with mock.patch("core.projects.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=128, stdout="")
            self.assertEqual(projects.resolve_project("/tmp/loose"), "/tmp/loose")

    def test_git_missing_or_error_falls_back_to_cwd(self):
        with mock.patch("core.projects.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(projects.resolve_project("/tmp/loose"), "/tmp/loose")

    def test_blank_cwd_returns_none(self):
        self.assertIsNone(projects.resolve_project(""))
        self.assertIsNone(projects.resolve_project(None))

    def test_label_is_basename(self):
        self.assertEqual(projects.project_label("/Users/me/repoA"), "repoA")
        self.assertEqual(projects.project_label("/"), "/")
        self.assertIsNone(projects.project_label(None))


class TestSetUserVarEscape(unittest.TestCase):
    def test_escape_is_osc1337_with_base64_value(self):
        esc = projects.build_setuservar_escape("mcp_project", "/Users/me/repoA")
        b64 = base64.b64encode(b"/Users/me/repoA").decode()
        self.assertEqual(esc, f"\033]1337;SetUserVar=mcp_project={b64}\007")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail** — `python -m unittest tests.test_projects -v` → `ModuleNotFoundError: core.projects`.

- [ ] **Step 3: Implement the module**

```python
"""Project identity for iTerm sessions.

The authoritative key is the iTerm2 session variable ``user.mcp_project``.
This module derives a stable project id from a CWD (the git repo root) and
builds the iTerm ``SetUserVar`` escape an agent uses to declare its project.

Env-assumption findings (Task 1 of the plan):
    SetUserVar form: ESC ] 1337 ; SetUserVar=<name>=<base64(value)> BEL
    (record any correction discovered during verification here)
"""

import base64
import os
import subprocess
from typing import Optional

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
```

- [ ] **Step 4: Run to verify they pass** — `python -m unittest tests.test_projects -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add core/projects.py tests/test_projects.py
git commit -m "feat(projects): git-root project resolver + SetUserVar escape builder"
```

---

### Task 3: `core/projects.py` — read + sticky-pin a session's project

**Files:**
- Modify: `core/projects.py`
- Test: `tests/test_projects.py` (extend)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_projects.py`)

```python
import asyncio


class TestSessionProject(unittest.IsolatedAsyncioTestCase):
    def _conn(self):
        return mock.MagicMock()  # opaque connection handle

    async def test_returns_existing_declared_project_without_pinning(self):
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="/Users/me/repoB")) as getv, \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock()) as setv:
            got = await projects.get_session_project(self._conn(), "sess-1")
        self.assertEqual(got, "/Users/me/repoB")
        setv.assert_not_awaited()  # already declared -> never overwrite (sticky)

    async def test_infers_and_pins_when_unset(self):
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="")) as getv, \
             mock.patch("core.projects.get_session_path", new=mock.AsyncMock(return_value="/Users/me/repoA/sub")) as getpath, \
             mock.patch("core.projects.resolve_project", return_value="/Users/me/repoA"), \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock(return_value=True)) as setv:
            got = await projects.get_session_project(self._conn(), "sess-2")
        self.assertEqual(got, "/Users/me/repoA")
        setv.assert_awaited_once_with(self._conn.__self__ if False else mock.ANY, "sess-2", "user.mcp_project", "/Users/me/repoA")

    async def test_returns_none_and_does_not_pin_when_no_cwd(self):
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.projects.get_session_path", new=mock.AsyncMock(return_value=None)), \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock()) as setv:
            got = await projects.get_session_project(self._conn(), "sess-3")
        self.assertIsNone(got)
        setv.assert_not_awaited()
```

- [ ] **Step 2: Run to verify they fail** — `python -m unittest tests.test_projects -v` → `AttributeError: ... get_session_project`.

- [ ] **Step 3: Implement** (append to `core/projects.py`)

```python
# Reuse the existing iTerm var/path helpers (already used elsewhere in the repo).
from core.iterm_path_monitor import (  # noqa: E402
    get_user_variable,
    set_user_variable,
    get_session_path,
)


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
    await set_user_variable(connection, session_id, f"user.{PROJECT_VAR}", project)
    return project
```

Note: `get_user_variable(connection, session_id, name)` prefixes `user.` if absent (per the module), so passing `PROJECT_VAR` ("mcp_project") reads `user.mcp_project`. `set_user_variable` is called with the explicit `user.` form to match its signature usage elsewhere.

- [ ] **Step 4: Run to verify pass** — `python -m unittest tests.test_projects -v` → PASS. (Fix the deliberately-awkward `mock.ANY` connection arg in the test if needed: assert `setv.await_args.args[1:] == ("sess-2", "user.mcp_project", "/Users/me/repoA")`.)

- [ ] **Step 5: Commit**

```bash
git add core/projects.py tests/test_projects.py
git commit -m "feat(projects): sticky read+pin of a session's project (first-observation-wins)"
```

---

### Task 4: `iterm-mcp project set/get` CLI

**Files:**
- Create: `iterm_mcpy/project_cli.py`
- Modify: `iterm_mcpy/main.py`
- Test: `tests/test_project_cli.py`

**Marker file:** the declared marker lives at `~/.iterm-mcp/projects/<key>` where `<key>` is the CC session id (`$CLAUDE_SESSION_ID` if available per Task 1, else the value of `--session-id` passed by the hook). The marker's content is the declared project path.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the `iterm-mcp project` CLI (set/get)."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from iterm_mcpy import project_cli


class TestProjectCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(project_cli, "MARKER_DIR", os.path.join(self.tmp.name, "projects"))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_set_emits_escape_and_writes_marker(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "cc-1"}), redirect_stdout(out):
            project_cli.cmd_set("/Users/me/repoA", session_id=None)
        # escape printed for iTerm to consume
        self.assertIn("SetUserVar=mcp_project=", out.getvalue())
        # marker written, keyed by CLAUDE_SESSION_ID
        marker = os.path.join(self.tmp.name, "projects", "cc-1")
        self.assertTrue(os.path.exists(marker))
        with open(marker) as fh:
            self.assertEqual(fh.read().strip(), "/Users/me/repoA")

    def test_set_uses_explicit_session_id_when_env_absent(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            project_cli.cmd_set("/Users/me/repoA", session_id="explicit-9")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "projects", "explicit-9")))

    def test_get_reads_marker(self):
        os.makedirs(os.path.join(self.tmp.name, "projects"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "projects", "cc-1"), "w") as fh:
            fh.write("/Users/me/repoA\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "cc-1"}), redirect_stdout(out):
            project_cli.cmd_get(session_id=None)
        self.assertIn("/Users/me/repoA", out.getvalue())

    def test_get_when_undeclared_prints_none(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "nope"}), redirect_stdout(out):
            project_cli.cmd_get(session_id=None)
        self.assertIn("not set", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify fail** — `python -m unittest tests.test_project_cli -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement `iterm_mcpy/project_cli.py`**

```python
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
```

- [ ] **Step 4: Wire into `iterm_mcpy/main.py`.** In `main()` add a subparser group (establishing the first nested-subcommand in this file):

```python
    p_project = sub.add_parser("project", help="declare/inspect a session's project")
    p_project_sub = p_project.add_subparsers(dest="project_command")
    p_pset = p_project_sub.add_parser("set", help="declare the repo you're working on")
    p_pset.add_argument("repo")
    p_pset.add_argument("--session-id", default=None)
    p_pget = p_project_sub.add_parser("get", help="show this session's declared project")
    p_pget.add_argument("--session-id", default=None)
```

and a dispatch branch (lazy import, matching the file's convention):

```python
    elif args.command == "project":
        from iterm_mcpy import project_cli
        if args.project_command == "set":
            project_cli.cmd_set(args.repo, session_id=args.session_id)
        elif args.project_command == "get":
            project_cli.cmd_get(session_id=args.session_id)
        else:
            parser.parse_args(["project", "--help"])
```

- [ ] **Step 5: Add a CLI-routing test** to `tests/test_cli.py` (patch the *source module*, per the file's convention):

```python
    def test_project_set_routes_to_cmd_set(self):
        with mock.patch("iterm_mcpy.project_cli.cmd_set") as cset:
            self._run(["project", "set", "/Users/me/repoA"])
        cset.assert_called_once_with("/Users/me/repoA", session_id=None)
```

- [ ] **Step 6: Run** — `python -m unittest tests.test_project_cli tests.test_cli -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add iterm_mcpy/project_cli.py iterm_mcpy/main.py tests/test_project_cli.py tests/test_cli.py
git commit -m "feat(cli): iterm-mcp project set/get (declare project via SetUserVar + marker)"
```

---

### Task 5: The ask-once `UserPromptSubmit` declaration hook

**Files:**
- Create: `hooks/project_declare.py`, `hooks/project_declare.sh`
- Modify: `docs/examples/hooks-settings.json`
- Test: `tests/test_project_hook.py`

**Behavior:** each user turn, the hook reads the CC hook JSON on stdin (which includes `session_id`). If the session's marker exists (declared) → emit empty `UserPromptSubmit` JSON (no-op). Else, up to `MAX_PROMPTS` (default 2) times, inject `additionalContext` telling the agent to run `iterm-mcp project set <its-repo> --session-id <session_id>`; track the count in `~/.iterm-mcp/projects/<session_id>.asked`. After the cap, stop (the on-demand server inference will pin a fallback).

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the project-declaration UserPromptSubmit hook (headless)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from hooks import project_declare as ph


class TestProjectDeclareHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(ph, "MARKER_DIR", os.path.join(self.tmp.name, "projects"))
        self.p.start()
        self.addCleanup(self.p.stop)
        os.makedirs(ph.MARKER_DIR, exist_ok=True)

    def _decide(self, session_id):
        return ph.decide({"session_id": session_id, "hook_event_name": "UserPromptSubmit"})

    def test_injects_when_undeclared(self):
        out = self._decide("s1")
        self.assertIn("additionalContext", out["hookSpecificOutput"])
        self.assertIn("iterm-mcp project set", out["hookSpecificOutput"]["additionalContext"])

    def test_noop_when_declared(self):
        open(os.path.join(ph.MARKER_DIR, "s2"), "w").close()
        out = self._decide("s2")
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_stops_after_max_prompts(self):
        for _ in range(ph.MAX_PROMPTS):
            self.assertIn("additionalContext", self._decide("s3")["hookSpecificOutput"])
        # next turn: capped -> no longer injects
        self.assertNotIn("additionalContext", self._decide("s3")["hookSpecificOutput"])

    def test_shape_is_userpromptsubmit(self):
        out = self._decide("s4")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
```

- [ ] **Step 2: Run to verify fail** — `python -m unittest tests.test_project_hook -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement `hooks/project_declare.py`** (stdlib only — runs as a CC hook)

```python
"""UserPromptSubmit hook: nudge an agent to declare its project (once)."""
import json
import os
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


def _marker(sid: str) -> Path:
    return Path(MARKER_DIR) / sid


def _asked_path(sid: str) -> Path:
    return Path(MARKER_DIR) / f"{sid}.asked"


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
```

- [ ] **Step 4: Implement `hooks/project_declare.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python "$(dirname "$0")/project_declare.py"
```

Make it executable: `chmod +x hooks/project_declare.sh`.

- [ ] **Step 5: Add an example wiring** to `docs/examples/hooks-settings.json` under `hooks.UserPromptSubmit` (example only — do NOT modify any real settings file): an entry running `hooks/project_declare.sh` with `timeout: 5`.

- [ ] **Step 6: Run** — `python -m unittest tests.test_project_hook -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add hooks/project_declare.py hooks/project_declare.sh docs/examples/hooks-settings.json tests/test_project_hook.py
git commit -m "feat(hooks): ask-once UserPromptSubmit hook to declare a session's project"
```

---

### Task 6: `project=` filter on the `sessions` list path

**Files:**
- Modify: `iterm_mcpy/tools/sessions.py` (`_GET_CORE_PARAMS`, `_list_sessions_core`, top-level `sessions(...)`)
- Modify: `core/models.py` (`SessionInfo`)
- Test: `tests/test_sessions.py` (extend)

- [ ] **Step 1: Write the failing test** (append to `tests/test_sessions.py`, reusing its `_make_ctx`)

```python
class TestSessionsProjectFilter(unittest.TestCase):
    def _session(self, sid, project):
        s = MagicMock(); s.id = sid; s.name = sid
        s.get_cwd = AsyncMock(return_value="/x")
        return s

    def test_filters_by_project(self):
        from iterm_mcpy.tools.sessions import sessions
        terminal = MagicMock()
        terminal.sessions = {"a": self._session("a", None), "b": self._session("b", None)}
        reg = MagicMock(); reg.get_agent_by_session = MagicMock(return_value=None)
        ctx = _make_ctx(terminal=terminal, agent_registry=reg)
        # mock get_session_project: 'a' -> repoA, 'b' -> repoB
        async def fake_proj(conn, sid):
            return "/repoA" if sid == "a" else "/repoB"
        with patch("iterm_mcpy.tools.sessions.get_session_project", new=fake_proj):
            parsed = asyncio.run(sessions(ctx=ctx, op="GET", project="/repoA"))
        ids = [s["session_id"] for s in parsed["data"]]
        self.assertEqual(ids, ["a"])
```

- [ ] **Step 2: Run to verify fail** — `python -m unittest tests.test_sessions.TestSessionsProjectFilter -v` → fails (param dropped / filter absent).

- [ ] **Step 3: Implement the three coordinated edits** (exact locations from the integration map):
  1. Add `"project"` to `_GET_CORE_PARAMS` (sessions.py ~:725-737).
  2. Add `project: Optional[str] = None` to `_list_sessions_core` (~:128-143). Near the existing identity-filter loop (~:232-244), after computing the per-session project, `continue` when `project` is given and doesn't match:
     ```python
     from core.projects import get_session_project  # top-of-file import
     ...
     session_project = await get_session_project(terminal.connection, session.id)
     if project is not None and session_project != project:
         continue
     ```
     Populate the new `SessionInfo(..., project=session_project)` field at the construction site (~:317-327).
  3. Add `project: Optional[str] = None` to the top-level `sessions(...)` signature (near `team`, ~:1582) and to its `raw_params` dict (~:1675+).
- [ ] **Step 4: Add `project: Optional[str] = None`** to `SessionInfo` in `core/models.py` (~:933-964).
- [ ] **Step 5: Run** — `python -m unittest tests.test_sessions -v` → PASS (existing + new).
- [ ] **Step 6: Commit**

```bash
git add iterm_mcpy/tools/sessions.py core/models.py tests/test_sessions.py
git commit -m "feat(sessions): project= filter + project field on session list output"
```

---

### Task 7: `projects` MCP tool — list sessions grouped by project

**Files:**
- Create: `iterm_mcpy/tools/projects.py`
- Modify: `iterm_mcpy/tools/__init__.py` (register the new tool)
- Test: `tests/test_projects_tool.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the `projects` MCP tool (grouping)."""
import asyncio, unittest
from unittest.mock import AsyncMock, MagicMock, patch
from iterm_mcpy.tools.projects import projects as projects_tool


def _ctx(terminal):
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"terminal": terminal, "logger": MagicMock(),
                                            "agent_registry": MagicMock()}
    return ctx


class TestProjectsTool(unittest.TestCase):
    def _s(self, sid):
        s = MagicMock(); s.id = sid; s.name = sid; return s

    def test_groups_sessions_by_project(self):
        terminal = MagicMock(); terminal.sessions = {"a": self._s("a"), "b": self._s("b"), "c": self._s("c")}
        async def fake_proj(conn, sid):
            return {"a": "/repoA", "b": "/repoA", "c": "/repoB"}[sid]
        with patch("iterm_mcpy.tools.projects.get_session_project", new=fake_proj):
            parsed = asyncio.run(projects_tool(ctx=_ctx(terminal), op="GET"))
        groups = {g["project"]: sorted(g["sessions"]) for g in parsed["data"]}
        self.assertEqual(groups, {"/repoA": ["a", "b"], "/repoB": ["c"]})

    def test_options_returns_schema(self):
        parsed = asyncio.run(projects_tool(ctx=_ctx(MagicMock(sessions={})), op="OPTIONS"))
        self.assertEqual(parsed["method"], "OPTIONS")
```

- [ ] **Step 2: Run to verify fail** — ModuleNotFoundError.

- [ ] **Step 3: Implement `iterm_mcpy/tools/projects.py`** following the action-tool pattern (read `subscribe.py`/`telemetry.py` for the OPTIONS-early-return + `ok_envelope` shape):

```python
"""`projects` tool — list iTerm sessions grouped by their project tag."""
from collections import defaultdict

from iterm_mcpy.responses import ok_envelope, err_envelope
from core.projects import get_session_project, project_label

_OPTIONS = {
    "tool": "projects", "kind": "action",
    "ops": {"GET": "list sessions grouped by project", "OPTIONS": "this schema"},
}


async def projects(ctx, op: str = "GET", **kwargs):
    if str(op).upper() == "OPTIONS":
        return ok_envelope(method="OPTIONS", data=_OPTIONS)
    lifespan = ctx.request_context.lifespan_context
    terminal = lifespan["terminal"]
    logger = lifespan.get("logger")
    try:
        groups = defaultdict(list)
        for session in list(terminal.sessions.values()):
            proj = await get_session_project(getattr(terminal, "connection", None), session.id)
            groups[proj or "(unassigned)"].append(session.id)
        data = [
            {"project": p, "label": project_label(p) if p != "(unassigned)" else p,
             "sessions": sids, "count": len(sids)}
            for p, sids in sorted(groups.items())
        ]
        return ok_envelope(method="GET", data=data)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.error("projects tool error: %s", exc)
        return err_envelope(method="GET", code="internal", message=str(exc))


def register(mcp):
    mcp.tool(name="projects")(projects)
```

(Confirm `ok_envelope`/`err_envelope` signatures against `iterm_mcpy/responses.py` and adjust the `err_envelope` kwargs to match the real signature.)

- [ ] **Step 4: Register** in `iterm_mcpy/tools/__init__.py` — add `projects` to the `from . import (...)` and the `_MODULES` list.

- [ ] **Step 5: Run** — `python -m unittest tests.test_projects_tool -v` → PASS. Then `python -m unittest tests.test_responses tests.test_sessions -v` to confirm no envelope/registration regressions.

- [ ] **Step 6: Commit**

```bash
git add iterm_mcpy/tools/projects.py iterm_mcpy/tools/__init__.py tests/test_projects_tool.py
git commit -m "feat(projects): projects MCP tool listing sessions grouped by project"
```

---

### Task 8: Document the future seams (no behavior change)

**Files:**
- Modify: `core/bus.py` (comment only), `core/projects.py` (seam stub)

- [ ] **Step 1:** In `core/bus.py:_resolve_fan_out_recipients`, add a comment at the `team:` branch noting the exact future `project:` insertion: *"Future: `project:<id>` fan-out mirrors team: — needs `agent_registry.list_agents(project=...)`, which requires a first-class `Agent.project` field (deferred)."* No code change.
- [ ] **Step 2:** In `core/projects.py`, add a documented seam (no implementation) so the manager phase has the shape:

```python
# --- Future seam (manager phase): per-project activity summaries ---
# def project_summary(project_id: str, *, since=None) -> dict:
#     """Digest recent activity for a project (captured actions + notifications +
#     bus messages stamped with this project) for a per-project manager agent.
#     Built in the manager phase; the `project` stamp on those streams is what
#     makes it queryable."""
```
- [ ] **Step 3: Commit**

```bash
git add core/bus.py core/projects.py
git commit -m "docs(projects): document project: bus fan-out + project_summary seams (deferred)"
```

---

## Self-Review

**Spec coverage:** project identity (Task 2) ✓; declared-first via SetUserVar + marker (Tasks 2/4) ✓; git-root fallback + sticky pin (Task 3) ✓; ask-once hook (Task 5) ✓; "stop checking once set" = sticky pin never overwrites + marker stops the hook (Tasks 3/5) ✓; query/targeting surface = `sessions project=` filter + `projects` listing (Tasks 6/7) ✓; B handoff = **out of v1 scope** (spec lists it as opt-in/later — note it remains a separate plan) — *gap by design, called out here*; future seams documented (Task 8) ✓. The spec's "path-monitor first-observation-wins" is satisfied by the on-demand pin (deviation #1).

**Placeholder scan:** no TBD/"handle edge cases"/"similar to Task N"; every code step has complete code. Two explicit verification points (Task 1) are a spike, not placeholders. Integration edits in Task 6 cite exact line ranges from the integration map and show the inserted code.

**Type consistency:** `PROJECT_VAR`/`get_session_project`/`build_setuservar_escape`/`resolve_project`/`project_label` are defined in Task 2/3 and used consistently in Tasks 4/6/7; `MARKER_DIR` is the shared marker location across `project_cli.py` (Task 4) and `project_declare.py` (Task 5), both keyed by the CC session id.

**Out-of-scope (YAGNI, deferred):** B handoff tool; `Agent.project` field + persistence; `project:` bus addressing; per-project visual profiles; `project_summary()` implementation; background path monitor.
