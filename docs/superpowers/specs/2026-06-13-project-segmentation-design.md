# CWD-Based Project Segmentation — Design

**Date:** 2026-06-13
**Status:** Design (pending user review → implementation plan)

## Goal

Segment iTerm2 sessions into **projects** so they can be queried, targeted (messaging/orchestration), and optionally visually grouped. The hard constraint: some agents run *inside* one repo's directory while actually *working on* a different repo, and some reset their CWD every turn — so the live CWD is an unreliable, noisy project signal.

## Guiding decisions (from brainstorming)

- **A (segment in place), with B (explicit handoff) as the recommended escalation.** Never move a live agent automatically — a Claude Code conversation is welded to the directory it was started in (proven: `~/.claude/projects/<cwd>/<id>.jsonl`; `--resume <id>` is "scoped to the current project directory and its git worktrees" and reports *No conversation found* elsewhere — `code.claude.com/docs/en/sessions.md`). So "moving" a live agent is impossible; we segment in place and offer an explicit, lossy handoff when true re-anchoring is needed.
- **Declared-first, git-root fallback.** The project tag is authoritative when declared; inferred from the git repo root otherwise.
- **Ask the agent, once, via a Claude Code hook.** When a session's project is unset, a hook prompts the agent to declare it; once set, we stop asking and stop re-checking (sticky).

## The authoritative key

A single iTerm2 session variable, **`user.mcp_project`**, holds the project (the absolute git-repo-root path; display label = its basename). Everything keys off this variable. It is set by three paths, in priority:

1. **At creation (force).** When *we* launch an agent (orchestrate / `create_window` with a `project=` argument), we know the intended project and set `user.mcp_project` immediately.
2. **Declared by the agent (force).** A session sets its own `user.mcp_project` via iTerm2's `SetUserVar` escape — `printf '\e]1337;SetUserVar=mcp_project=<base64(path)>\a'`, wrapped as `iterm-mcp project set <repo>`. This is how an agent whose CWD lies tells us its real project. *(To confirm at plan time: exact SetUserVar escape framing.)*
3. **Inferred (finesse, fallback).** For sessions that never declare, the path monitor resolves the **git repo root** of the first stable CWD and sets `user.mcp_project` **once**.

**Sticky / stop-checking:** once `user.mcp_project` is set by any path, it is never overwritten by later CWD changes. An explicit declaration is immutable to CWD noise. This is the fix for the per-turn reset thrash.

## Components

### 1. Project identity resolver — `core/projects.py`
- `resolve_project(cwd: str) -> str`: returns `git rev-parse --show-toplevel` of `cwd`; falls back to `cwd` itself for non-git dirs. Pure, cheap, cached.
- `project_label(project_id: str) -> str`: basename for display.
- Project ↔ sessions grouping helpers (group the agent registry's sessions by their `user.mcp_project`). May fold into the existing agent registry rather than a new module if cleaner.

### 2. The "ask once" Claude Code hook
- A **`UserPromptSubmit` hook** installed in agents. Each turn, until the project is declared, it injects a one-time `additionalContext`: *"Declare the repo/project you are working on by running `iterm-mcp project set <repo-path>`."* Once declared, it is a no-op (stops asking).
- "Declared?" is tracked per session by a small flag (a state file keyed by session id, mirroring the mc-coercion counter pattern, written by `iterm-mcp project set`) so the hook is cheap and self-contained — no server↔agent round trip.
- **Fallback / no-nag:** after N (default 2) unanswered prompts, the hook stops injecting; the server-side inference (below) then assigns the git-root so the session is never left unassigned.
- *Design choice to confirm at plan time:* self-checking hook (recommended, decoupled) vs. an iTerm path-change trigger that injects. The self-checking hook covers both "starts mislocated and never moves" and "resets every turn" without cross-process plumbing.

### 3. Path monitor changes — `core/iterm_path_monitor.py`
- Today it re-evaluates (and re-assigns teams) on **every** `path` change → this is the thrash source. Change to **first-observation-wins**: set `user.mcp_project` (via git-root inference) only if it is unset; never overwrite on later changes.
- Keep watching `path` only to (a) seed the one-time inference for undeclared sessions and (b) feed CWD telemetry — not to re-segment.

### 4. `iterm-mcp project` CLI + MCP surface
- CLI: `iterm-mcp project set <repo>` (emit SetUserVar for the current session + write the declared flag), `project get` (print current session's project), `project list` (sessions grouped by project).
- MCP: a `project=` **filter** on the existing `sessions`/`agents` tools, a `projects` **listing** (groups + counts), and a `project=` **selector** for messaging/orchestrate targeting ("everything in project X"), alongside the existing team/agent selectors.

### 5. (Phase 2, optional) Visual grouping
- A per-project dynamic profile (distinct tab color / badge) reusing the `MCP-TEST` profile machinery (`core/test_window_tracker.ensure_*`), so windows are eyeball-grouped by project. Deferred — not required for the core metadata/targeting value.

### 6. B — explicit handoff (opt-in)
- A tool/command that: (1) asks the current agent to emit a structured **context summary**, (2) launches a **fresh** agent *in* repo B seeded with that summary (a new conversation — lossy by necessity, since the original can't be moved), (3) parks or closes the old agent per the caller's choice.
- Clearly an explicit action, never automatic. The git-worktree case (repo B is a worktree of the agent's repo → `--resume` does cross it) is the one narrow exception where a near-lossless move is possible; note it but don't special-case it in v1.

## Data flow

```
launch (orchestrate)  ──set──▶  user.mcp_project  ◀──set── agent: `iterm-mcp project set` (via hook prompt)
                                      │  ▲
                                      │  └──set-if-unset── PathMonitor (git-root of first stable CWD)
                                      ▼
                         registry groups sessions by project
                                      │
                 ┌────────────────────┼─────────────────────┐
              query (projects/      target (project=         visual (Phase 2:
              sessions filter)      messaging/orchestrate)   per-project profile)
```

## Edge cases

- **Non-git CWD** → project = the directory itself (still a stable key).
- **Agent ignores the prompt** → after N tries, git-root inference assigns it; never left unassigned.
- **Agent legitimately switches to a different real repo** → sticky means it keeps its first project; to move it, the agent re-declares (`iterm-mcp project set`) — explicit, which is correct (we don't want CWD churn to re-segment).
- **Multiple agents, same project** → grouped together; targeting addresses all of them.
- **Foreign / user-opened windows** → inferred via git-root like any undeclared session; never forced into a project they don't belong to.
- **CWD resets between two real projects every turn** → the declaration (asked once) pins the true one; CWD noise is ignored thereafter.

## Non-goals (YAGNI)

- No attempt to change a running process's CWD or move a live conversation (proven impossible / unsupported).
- No automatic re-anchoring — B is always explicit.
- Phase-2 visual grouping is optional and deferred.

## Testing

All headless (honoring the test-safety rule — **no live iTerm windows**, no full-suite runs):
- `resolve_project` / `project_label` — git-root resolution, non-git fallback, caching (use temp git repos / mocked `git`).
- Sticky logic — set-if-unset; never overwrites; declaration beats inference.
- The hook's "ask once then stop" state machine — unset → inject → declared flag set → no-op; N-try fallback (mock the flag/state, no real CC).
- `iterm-mcp project set/get/list` CLI routing (mock the SetUserVar emit; assert the escape is emitted for the current session).
- Project grouping/targeting in the registry/MCP surface (mocked sessions).

## To confirm at plan time

1. Exact `SetUserVar` escape framing and base64 expectation.
2. How the `UserPromptSubmit` hook reads/knows "project already declared" (flag file vs. querying the iTerm var) — pick the cheapest reliable signal.
3. How the hook ships to agents (the triggers/hooks installation path — ties into the `iterm-mcp triggers` work).
4. Whether `iterm-mcp project` is a new CLI subcommand group (recommended) or folded into `sessions`.
