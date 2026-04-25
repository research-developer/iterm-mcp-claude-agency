# Repository Root Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De-clutter the repository root by archiving historical planning docs, deleting stale developer scratch files, relocating reference material, and refreshing CLAUDE.md so structure docs match reality.

**Architecture:** Pure file/directory reorganization plus a small CLAUDE.md edit. No code changes, no behavior changes. Each task is one logical move with a verification step (build still imports, tests still discover, README link still resolves) and an isolated commit. Work happens on a single short-lived branch `chore/root-cleanup` against `main`.

**Tech Stack:** git, mv/rm, ripgrep, `python -m unittest discover` for test-discovery sanity check.

---

## Out-of-scope (explicit)

The following are NOT addressed by this plan and should be filed as separate work:

- Feedback **fb-20260424-157473f7** (UX feedback: layout default, named profiles, unwrapped JSON, etc.) — separate product enhancement plan.
- Feedback **fb-20260424-bf579b19** (user's macOS home-directory organization plan) — about user's `~/`, not this repo.
- The 30+ stale remote branches on `origin/` — branch hygiene is a separate task.
- The dual gRPC + FastMCP transports under `iterm_mcpy/` — architectural decision, not cleanup.
- Removing the unused `protos/` directory or generated `*_pb2*.py` files — depends on the gRPC decision above.

---

## Inventory of Root-Level Cruft

Captured from `ls -la` at the start of work (commit `5720281`):

**A. Historical planning docs (8 files)** — keep history, but not at root:
- `EPIC_PROPOSAL_INDEX.md`, `EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md`, `EPIC_PROPOSAL_QUICK_REFERENCE.md`, `EPIC_PROPOSAL_README.md`, `EPIC_PROPOSAL_SUMMARY.md`
- `EPIC_RECOMMENDATION.md`, `EPIC_REVIEW_COMMENT.md`, `EPIC_STATUS.md`
- `AUDIT_SUMMARY.md`, `IMPROVEMENT_ROADMAP.md`, `FOLLOWUP_ISSUES.md`, `README_UPDATE_PLAN.md`

**B. Orphaned dev-scratch files (5 files)** — broken or stale, delete:
- `.claude-prompt.txt`, `.claude-runner.sh` — `.claude-runner.sh` hard-codes `/Users/preston/MCP/iterm-mcp-issue-65-...` (someone else's machine).
- `run_claude_agent.sh`, `create_layout.py` — `create_layout.py` references `claude_debugging_task.txt` which does not exist; `run_claude_agent.sh` is referenced only by `create_layout.py`.
- `AGENT.context-start-brief.txt` — orphaned briefing.

**C. Stray "tests" at root (3 files)** — hand-rolled REPL scripts, not unittest tests; behavior is covered by `tests/`. Delete:
- `test_cascade.py`, `test_lock.py`, `test_playbook.py`

**D. Oversized reference doc at root:**
- `iterm-api-doc.md` (233 KB) — move to `docs/`.

**E. CLAUDE.md drift** — references `iterm_mcp_python/` and `server/` directories that no longer exist; "Recent Changes" section is from March 2025.

**F. Top-level `__init__.py`** — 33-byte file at repo root. Decide: keep (if Python tooling expects it) or remove. Investigated in Task 9.

---

## Branch Setup

- [ ] **Step 0.1: Confirm clean working tree**

Run:
```bash
git status
```
Expected: `nothing to commit, working tree clean` on `main`.

- [ ] **Step 0.2: Create cleanup branch**

Run:
```bash
git checkout -b chore/root-cleanup
```
Expected: `Switched to a new branch 'chore/root-cleanup'`.

---

## Task 1: Archive historical planning docs

**Files:**
- Create: `docs/archive/` (new directory)
- Move: `EPIC_PROPOSAL_INDEX.md`, `EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md`, `EPIC_PROPOSAL_QUICK_REFERENCE.md`, `EPIC_PROPOSAL_README.md`, `EPIC_PROPOSAL_SUMMARY.md`, `EPIC_RECOMMENDATION.md`, `EPIC_REVIEW_COMMENT.md`, `EPIC_STATUS.md`, `AUDIT_SUMMARY.md`, `IMPROVEMENT_ROADMAP.md`, `FOLLOWUP_ISSUES.md`, `README_UPDATE_PLAN.md` → `docs/archive/`

**Why archive instead of delete:** Multi-month epic and audit history has reference value (how decisions were made, what was scoped). Moving — not deleting — keeps `git log --follow` clean and lets future readers find them via `docs/archive/README.md`.

- [ ] **Step 1.1: Create the archive directory and index**

Run:
```bash
mkdir -p docs/archive
```

Then create `docs/archive/README.md`:

```markdown
# docs/archive

Historical planning documents kept for context. These are NOT current — they describe past initiatives, audits, and proposals that have either been completed or superseded.

For current state, see the top-level `README.md` and `CLAUDE.md`.

## Contents

- **EPIC_PROPOSAL_*.md** — multi-agent orchestration epic proposal (2024 Q4 — early 2025); primary objectives shipped per `EPIC_STATUS.md`.
- **EPIC_STATUS.md** — closure report for the orchestration epic.
- **EPIC_RECOMMENDATION.md / EPIC_REVIEW_COMMENT.md** — review artifacts for the epic.
- **AUDIT_SUMMARY.md** — happy-cli test pattern audit (Dec 2024).
- **IMPROVEMENT_ROADMAP.md** — strategic roadmap snapshot at the close of the epic.
- **FOLLOWUP_ISSUES.md** — enhancement candidates spun out of the epic.
- **README_UPDATE_PLAN.md** — TDD-style README rewrite plan; superseded by the current README.
```

- [ ] **Step 1.2: Move all 12 docs**

Run:
```bash
git mv \
  EPIC_PROPOSAL_INDEX.md \
  EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md \
  EPIC_PROPOSAL_QUICK_REFERENCE.md \
  EPIC_PROPOSAL_README.md \
  EPIC_PROPOSAL_SUMMARY.md \
  EPIC_RECOMMENDATION.md \
  EPIC_REVIEW_COMMENT.md \
  EPIC_STATUS.md \
  AUDIT_SUMMARY.md \
  IMPROVEMENT_ROADMAP.md \
  FOLLOWUP_ISSUES.md \
  README_UPDATE_PLAN.md \
  docs/archive/
```
Expected: 12 files moved; `git status` shows 12 renames.

- [ ] **Step 1.3: Update the one README link that points to a moved doc**

`README.md:18` currently reads:
```markdown
See [EPIC_STATUS.md](EPIC_STATUS.md) for detailed implementation status.
```

Replace with:
```markdown
See [EPIC_STATUS.md](docs/archive/EPIC_STATUS.md) for the historical implementation status of the multi-agent orchestration epic.
```

- [ ] **Step 1.4: Verify no other references to moved docs are broken**

Run:
```bash
rg -n "EPIC_PROPOSAL|EPIC_STATUS|EPIC_RECOMMENDATION|EPIC_REVIEW_COMMENT|AUDIT_SUMMARY|IMPROVEMENT_ROADMAP|FOLLOWUP_ISSUES|README_UPDATE_PLAN" --glob '!docs/archive/**' --glob '!docs/superpowers/plans/**' --glob '!.git/**'
```
Expected: Only the updated `README.md:18` line should match. Internal cross-links between the moved docs are fine — they still resolve inside `docs/archive/`.

- [ ] **Step 1.5: Commit**

```bash
git add docs/archive/ README.md
git commit -m "chore: archive historical epic and audit docs to docs/archive/"
```

---

## Task 2: Delete orphaned dev-scratch files

**Files (delete):**
- `.claude-prompt.txt`
- `.claude-runner.sh`
- `run_claude_agent.sh`
- `create_layout.py`
- `AGENT.context-start-brief.txt`

**Why delete (not archive):** All five are broken — they reference paths that don't exist on this machine or files that aren't checked in. They have no historical reference value (reproducible from the prompt that produced them). Git history retains them if anyone ever needs to look.

- [ ] **Step 2.1: Confirm none are referenced from live code**

Run:
```bash
rg -n '\.claude-runner\.sh|\.claude-prompt\.txt|run_claude_agent\.sh|create_layout\.py|AGENT\.context-start-brief' --glob '!.git/**' --glob '!docs/archive/**' --glob '!docs/superpowers/plans/**'
```
Expected output: only self-references inside `.claude-runner.sh` and `create_layout.py`. No reference from `pyproject.toml`, `README.md`, `CLAUDE.md`, `iterm_mcpy/`, `core/`, `tests/`, or any installed-script entry point.

If anything else matches, STOP and surface the reference — do not delete.

- [ ] **Step 2.2: Delete the five files**

Run:
```bash
git rm .claude-prompt.txt .claude-runner.sh run_claude_agent.sh create_layout.py AGENT.context-start-brief.txt
```
Expected: 5 files staged for deletion.

- [ ] **Step 2.3: Verify package still imports**

Run:
```bash
python -c "import iterm_mcpy; import core" 2>&1
```
Expected: no output, exit 0. (No deleted file is part of either package.)

- [ ] **Step 2.4: Commit**

```bash
git commit -m "chore: remove broken dev-scratch scripts from repo root

Five files referenced paths that no longer exist (someone else's
machine path in .claude-runner.sh, missing claude_debugging_task.txt
in create_layout.py) or were one-off context briefs. Recoverable
from git history if ever needed."
```

---

## Task 3: Remove stray manual-test scripts at root

**Files (delete):**
- `test_cascade.py` — manual REPL script using `print(f'PASS: {passed}')`; behavior covered by `tests/test_action_tools.py::test_cascade_*` and `tests/test_agent_registry.py::test_cascade_priority`.
- `test_lock.py` — manual REPL script; behavior covered by `tests/test_sessions.py::test_lock_*` and `tests/test_session_tags.py::test_lock_*`.
- `test_playbook.py` — manual REPL script; behavior covered by `tests/test_models.py::test_playbook_structure`.

**Why delete:** They are not picked up by `python -m unittest discover` (they live outside `tests/` and lack `unittest.TestCase` classes), they print rather than assert, and the same surface is exercised by the real test modules listed above. They add noise without coverage.

- [ ] **Step 3.1: Confirm coverage exists in the real test suite**

Run:
```bash
rg -n "def test_(cascade|lock|playbook)" tests/
```
Expected: at least one match for each of `cascade`, `lock`, `playbook`. (If any category has zero matches, pause and assess before deleting that file.)

- [ ] **Step 3.2: Confirm nothing imports them**

Run:
```bash
rg -n "from test_(cascade|lock|playbook)|import test_(cascade|lock|playbook)" --glob '!.git/**'
```
Expected: no output.

- [ ] **Step 3.3: Delete the three files**

Run:
```bash
git rm test_cascade.py test_lock.py test_playbook.py
```
Expected: 3 files staged for deletion.

- [ ] **Step 3.4: Verify the real test suite still discovers tests**

Run:
```bash
python -m unittest discover -s tests -t . --locals 2>&1 | tail -3
```
Expected: a normal unittest summary line (e.g. `Ran N tests in X.XXXs`). Failures unrelated to this change are tolerated for this step (we are checking discovery, not pass-rate); the count should be roughly the same as before deletion. Specifically, `python -m unittest discover` should not error out with "no tests found".

- [ ] **Step 3.5: Commit**

```bash
git commit -m "chore: remove ad-hoc REPL test scripts from repo root

test_cascade.py, test_lock.py, test_playbook.py were manual print-based
scripts not picked up by unittest discovery. The same behavior is
exercised by tests/test_action_tools.py, tests/test_agent_registry.py,
tests/test_sessions.py, tests/test_session_tags.py, and
tests/test_models.py."
```

---

## Task 4: Move iterm-api-doc.md to docs/

**Files:**
- Move: `iterm-api-doc.md` (233 KB) → `docs/iterm-api-reference.md`

**Why rename:** Existing docs in `docs/` use descriptive lower-kebab-case names (e.g. `claude-code-mcp-analysis.md`, `tool-noun-extraction.md`). `iterm-api-reference.md` matches that convention and reads better than `iterm-api-doc`.

- [ ] **Step 4.1: Verify reference scope**

Run:
```bash
rg -n "iterm-api-doc" --glob '!.git/**' --glob '!docs/superpowers/plans/**'
```
Expected: zero matches outside this plan. (If anything matches in `README.md` or elsewhere, capture the path — Step 4.3 will rewrite it.)

- [ ] **Step 4.2: Move the file**

Run:
```bash
git mv iterm-api-doc.md docs/iterm-api-reference.md
```

- [ ] **Step 4.3: Update any references found in 4.1**

If 4.1 surfaced references, edit each to `docs/iterm-api-reference.md`. If none, skip this step.

- [ ] **Step 4.4: Commit**

```bash
git add -A
git commit -m "chore: move iterm-api-doc.md into docs/ as iterm-api-reference.md"
```

---

## Task 5: Refresh CLAUDE.md to match current structure

**Files:**
- Modify: `CLAUDE.md`

The current CLAUDE.md has three accuracy problems:

1. **Build & Test Commands** section says `python -m server.main` — there is no `server/` package; the entry point is `python -m iterm_mcpy.main` (or `run_server.py`).
2. **Project Structure** section's tree shows `iterm_mcp_python/` — that directory was renamed to `iterm_mcpy/` and the structure has shifted (now includes `tools/` package, `dispatcher.py`, `responses.py`, `helpers.py`, `welcome_status.py`, gRPC files).
3. **Worktrees → Active Worktrees** table lists three worktrees (`refactor-tools`, `feat-parallel`, `10-auditadapt-...`) — `git worktree list` shows none of those exist locally any more.

The "Recent Changes (March 2025)" section is fine as a historical log and stays.

- [ ] **Step 5.1: Read the current CLAUDE.md sections that need editing**

Run:
```bash
rg -n "python -m server\.main|iterm_mcp_python|Active Worktrees|refactor-tools|feat-parallel|10-auditadapt" CLAUDE.md
```
Capture line numbers — they anchor the edits below.

- [ ] **Step 5.2: Replace `python -m server.main` references**

In CLAUDE.md, replace every literal occurrence of `python -m server.main` with `python -m iterm_mcpy.main`. There should be three matches: the `Run server` line, the `Run demo mode` line, and the "Running the Server" code block. The Run server / demo lines should read:

```markdown
- Run server: `python -m iterm_mcpy.main` (run the FastMCP server implementation)
- Run demo mode: `python -m iterm_mcpy.main --demo` (run the demo controller)
```

And the "Running the Server" block:

```bash
# Install dependencies
pip install -e .

# Launch server
python -m iterm_mcpy.main
```

- [ ] **Step 5.3: Replace the stale project-structure tree**

Find the existing "### Project Structure" code block and replace its body with the structure that matches reality on `main` as of `5720281`:

```
iterm-mcp/
├── pyproject.toml                # Python packaging configuration
├── .mcp.json                     # Claude Code plugin manifest
├── README.md
├── CHANGELOG.md
├── CLAUDE.md
├── core/                         # Core domain logic (sessions, agents, managers,
│   │                             #   feedback, memory, services, roles, flows,
│   │                             #   checkpointing, dashboard, telemetry,
│   │                             #   definer_verbs, profiles, tags, messaging)
│   └── ... (~22 modules)
├── iterm_mcpy/                   # MCP server package
│   ├── __init__.py
│   ├── main.py                   # CLI entry point
│   ├── fastmcp_server.py         # Lifespan, resources, prompts, register_all()
│   ├── mcp_server.py             # Lower-level server plumbing
│   ├── dispatcher.py             # MethodDispatcher (WebSpec method semantics)
│   ├── responses.py              # ok_envelope/err_envelope (method-tagged)
│   ├── helpers.py                # Shared helpers (resolve_session, execute_*)
│   ├── welcome_status.py
│   ├── grpc_server.py            # Optional gRPC transport
│   ├── grpc_client.py
│   ├── iterm_mcp_pb2*.py         # Generated proto stubs
│   └── tools/                    # 15 method-semantic tools
│       ├── __init__.py           # register_all(mcp) composes all tool modules
│       ├── _callbacks.py         # Shared manager-callback wiring (private)
│       │ # Collections (9)
│       ├── sessions.py           # session lifecycle, output, keys, tags, roles, locks, monitoring
│       ├── agents.py             # register/list/remove + notifications/hooks/locks
│       ├── teams.py              # create/list/remove + team membership
│       ├── managers.py           # create/list/remove managers + worker mgmt
│       ├── feedback.py           # submit/query/triage/fork + triggers/config
│       ├── memory.py             # store/retrieve/search/delete + stats
│       ├── services.py           # list/add/configure/start/stop + scoping
│       ├── roles.py              # read-only catalog of session roles
│       ├── workflows.py          # trigger/list/history workflow events
│       │ # Actions (6)
│       ├── messages.py           # cascade / hierarchical multi-session messaging
│       ├── orchestrate.py        # multi-step playbook execution
│       ├── delegate.py           # delegate task / execute plan through a manager
│       ├── wait_for.py           # long-poll for agent idle (GET)
│       ├── subscribe.py          # arm an output-pattern subscription
│       └── telemetry.py          # start/stop telemetry dashboard
├── tests/                        # unittest test suite (~40 modules)
├── docs/                         # Reference & guide docs
│   ├── archive/                  # Historical epic/audit/roadmap docs
│   └── superpowers/plans/        # Implementation plans (this file lives here)
├── skills/                       # Discovery skills for the plugin
│   ├── session-management/SKILL.md
│   ├── agent-orchestration/SKILL.md
│   └── feedback-workflow/SKILL.md
├── scripts/                      # Helper scripts (it2api samples, watchers)
├── examples/
├── protos/                       # gRPC proto sources
├── static/
└── utils/                        # Logging utilities
```

(Replace the entire existing tree block; do not leave fragments of the old one.)

- [ ] **Step 5.4: Refresh the "Active Worktrees" table**

Replace the existing 3-row table with:

```markdown
#### Active Worktrees

Run `git worktree list` to see currently active worktrees. None are committed long-lived; create them per feature using the convention below.
```

(Drop the table entirely — it was a snapshot, and snapshots in long-lived docs go stale fast. The "Conventions" subsection below it stays.)

- [ ] **Step 5.5: Verify the edits parse and renders look right**

Run:
```bash
rg -n "python -m server\.main|iterm_mcp_python|refactor-tools|feat-parallel|10-auditadapt" CLAUDE.md
```
Expected: zero matches.

Run:
```bash
head -1 CLAUDE.md && wc -l CLAUDE.md
```
Expected: file still starts with `# MCP (Model Context Protocol) Project Guide` and total line count is roughly within ±50 of the original (sanity bound — we're editing, not gutting).

- [ ] **Step 5.6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: refresh CLAUDE.md to match current package layout

- Replace stale 'python -m server.main' with 'python -m iterm_mcpy.main'
- Update project-structure tree to reflect iterm_mcpy/ rename, the
  tools/ package introduced in SP2, and the new docs/archive layout
- Drop the stale Active Worktrees snapshot table; defer to
  'git worktree list' instead"
```

---

## Task 6: Decide on root `__init__.py`

**Files:**
- Investigate, then either keep or delete `__init__.py` at repo root.

**Why a separate task:** A top-level `__init__.py` at a repo root is unusual and can confuse Python's import machinery (it makes the root itself an importable package, which can shadow installed packages). But it may exist deliberately to support `from core.x import y` style imports during local development. Don't move on assumption — verify.

- [ ] **Step 6.1: Inspect the file**

Run:
```bash
cat __init__.py
```
Expected: 33 bytes — confirm what it actually contains.

- [ ] **Step 6.2: Check whether pyproject.toml or any installed entry treats the root as a package**

Run:
```bash
rg -n "packages|package_dir|find_packages|find_namespace_packages" pyproject.toml
```
And:
```bash
python -c "import sys; sys.path.insert(0, '.'); import core; print(core.__file__)"
```
Expected: `pyproject.toml` should declare `core` and `iterm_mcpy` (and probably `utils`) as packages; `core` should import cleanly without needing a root `__init__.py`.

- [ ] **Step 6.3a: If the file is empty or trivial AND `pyproject.toml` does not list `.` (root) as a package, delete it**

```bash
git rm __init__.py
git commit -m "chore: remove unused root __init__.py

The repository root is not declared as a Python package in
pyproject.toml; the file was a leftover that risked shadowing
the iterm_mcpy/core packages on sys.path."
```

- [ ] **Step 6.3b: If the file has meaningful content OR the project relies on it, leave it in place and skip the commit.**

Document the finding in the task summary at the end of the plan execution rather than this file.

---

## Task 7: Final root-listing snapshot

**Files:**
- None modified.

- [ ] **Step 7.1: Verify root is meaningfully shorter**

Run:
```bash
ls -1 | sort
```
Expected: the listing should no longer include any of the 12 archived planning docs, the 5 deleted scratch files, the 3 deleted scratch tests, or `iterm-api-doc.md`. Approximate target: ~20 entries at root, down from 51.

Capture the listing in the PR/commit description for reviewer reference (do not check it into the repo).

- [ ] **Step 7.2: Run the full test suite one more time**

Run:
```bash
python -m unittest discover -s tests -t . 2>&1 | tail -5
```
Expected: same pass/fail counts as `main` before this branch — we did not touch any code.

- [ ] **Step 7.3: Push and open a PR**

Run:
```bash
git push -u origin chore/root-cleanup
gh pr create --title "chore: clean up repo root" --body "$(cat <<'EOF'
## Summary
- Move 12 historical epic/audit docs from repo root to `docs/archive/` with an index
- Delete 5 broken dev-scratch files (one referenced another user's hard-coded path)
- Delete 3 ad-hoc REPL test scripts whose behavior is covered by `tests/`
- Move `iterm-api-doc.md` (233 KB) into `docs/` as `iterm-api-reference.md`
- Refresh `CLAUDE.md` to match the current `iterm_mcpy/` + `tools/` layout
- Resolve the unused root `__init__.py`

No code or behavior changes. Test counts unchanged.

## Test plan
- [ ] `python -c "import iterm_mcpy; import core"` succeeds
- [ ] `python -m unittest discover -s tests -t .` reports the same totals as on `main`
- [ ] `rg "EPIC_PROPOSAL|EPIC_STATUS"` finds only intended references in `docs/archive/` and the updated `README.md` link
EOF
)"
```

---

## Self-Review (performed by author)

**1. Spec coverage:** The spec was the inventory at the top. Each of A/B/C/D/E/F maps to:
- A → Task 1 (archive)
- B → Task 2 (delete dev-scratch)
- C → Task 3 (delete REPL tests)
- D → Task 4 (move api-doc)
- E → Task 5 (refresh CLAUDE.md)
- F → Task 6 (decide on root __init__.py)
Coverage complete.

**2. Placeholder scan:** No `TBD`, `TODO`, "implement later", "fill in details", or "similar to Task N" in any step. Every code change shows the literal content. Every command shows the literal command and the expected output.

**3. Type / name consistency:** No types or function signatures introduced — pure file moves and one CLAUDE.md edit. Filenames are spelled identically across the inventory, the steps, and the commit messages (cross-checked: `EPIC_PROPOSAL_INDEX.md`, `iterm-api-reference.md`, `chore/root-cleanup`, `python -m iterm_mcpy.main`).

**4. Reversibility:** Every step is `git mv` / `git rm` / a single edit, all on a feature branch. Worst case: `git checkout main && git branch -D chore/root-cleanup` undoes everything.
