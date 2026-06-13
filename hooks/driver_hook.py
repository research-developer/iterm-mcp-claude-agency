#!/usr/bin/env python3
"""
ControIDE Phase-0 driver hook helper.

Called by stop.sh, pretooluse.sh, and userpromptsubmit.sh. Reads Claude Code
hook JSON from stdin, dispatches to the appropriate handler, and emits
structured hook-decision JSON to stdout.

Usage:
    driver_hook.py stop              # Stop hook
    driver_hook.py pretooluse        # PreToolUse hook
    driver_hook.py userpromptsubmit  # UserPromptSubmit hook

Multiple-choice (MC) coercion:
    When the flag file ~/.iterm-mcp/multiple-choice.on exists, the
    UserPromptSubmit hook injects an instruction asking Claude to end its
    response with a numbered multiple-choice list. The Stop hook checks whether
    the assistant turn contains options in that format; if not (and this is the
    first Stop for the current user turn), it blocks with a reprompt once.
    Toggle the feature with hooks/mc_toggle.sh on|off|status.

    State-dir override: set env MC_STATE_DIR to a directory path (used by tests
    so they don't touch ~/.iterm-mcp).

Error handling:
    If the dashboard is not running or /api/ask times out, the hook falls
    back to a safe default and does NOT crash Claude Code:
    - Stop hook fallback: emit {} (let Claude stop naturally)
    - PreToolUse hook fallback: emit allow (permissive default)
    - UserPromptSubmit fallback: emit minimal no-op JSON
    Errors are logged to stderr so they appear in Claude Code's hook logs.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

DASHBOARD_URL = os.environ.get("ITERM_MCP_DASHBOARD_URL", "http://127.0.0.1:9999")
HOOK_TIMEOUT = 120  # seconds to wait for human answer

STOP_OPTIONS = [
    {"id": "continue", "label": "Continue",  "text": "Continue with the plan you just described."},
    {"id": "refine",   "label": "Refine",    "text": "Pause and ask me a clarifying question before proceeding."},
    {"id": "stop",     "label": "Stop here", "text": ""},
    {"id": "custom",   "label": "Custom…",   "text": ""},
]

PRETOOLUSE_OPTIONS = [
    {"id": "allow", "label": "Allow",      "text": "allow"},
    {"id": "deny",  "label": "Deny",       "text": "deny"},
    {"id": "ask",   "label": "Ask (edit)", "text": "ask"},
]

# ---------------------------------------------------------------------------
# Multiple-choice coercion constants
# ---------------------------------------------------------------------------

# Regex that matches a numbered option line in any of the accepted formats:
#   1) Title: description
#   1. Title: description
#   (1) Title: description
#   **1)** Title: description    (bolded marker)
#   **1. Title: desc**           (whole line bolded)
#   - 1) Title: description      (leading bullet)
# At least one such line anywhere in the assistant turn passes detection.
OPTION_PATTERN = re.compile(r'^\s*(?:[-*]\s+)?\*{0,2}(?:\d+[.)]|\(\d+\))\*{0,2}\s+\S', re.MULTILINE)

# The instruction injected into every UserPromptSubmit when MC coercion is ON.
# Written to agree exactly with OPTION_PATTERN so a complying Claude passes.
MC_INSTRUCTION = (
    "Please end your response with a numbered list of 3-5 suggested next "
    "steps or options, formatted exactly as:\n\n"
    "  1) Option title: brief description\n"
    "  2) Option title: brief description\n"
    "  ...\n\n"
    "Use this exact format (number, closing parenthesis, space, title, colon, "
    "space, description) so the driver dashboard can display quick-select tiles."
)

# Reprompt reason injected into the Stop block decision when options are missing.
MC_REPROMPT_REASON = (
    "Your response is missing the required numbered options list. "
    "Please restate your answer and end with 3-5 numbered options in this format:\n"
    "  1) Option title: brief description\n"
    "  2) Option title: brief description\n"
    "  ...\n"
    "This allows the driver dashboard to display quick-select tiles."
)


# ---------------------------------------------------------------------------
# MC flag + state helpers (pure functions, importable for testing)
# ---------------------------------------------------------------------------


def _mc_flag_path() -> Path:
    """Return the path of the MC coercion toggle flag file.

    Returns:
        Path to ~/.iterm-mcp/multiple-choice.on
    """
    return Path.home() / ".iterm-mcp" / "multiple-choice.on"


def _mc_flag_on() -> bool:
    """Return True when the MC coercion toggle flag file exists.

    Returns:
        True if the flag file is present, False otherwise.
    """
    return _mc_flag_path().exists()


def _mc_state_dir() -> Path:
    """Return the directory used for per-session reprompt counters.

    The directory can be overridden via the MC_STATE_DIR environment variable
    so tests can use a temp directory without touching ~/.iterm-mcp.

    Also opportunistically prunes counter files whose mtime is older than
    7 days.  The prune is cheap (one stat per file) and fully exception-safe:
    any failure is silently ignored so a broken prune never crashes the hook.

    Returns:
        Path to the state directory (created if absent).
    """
    override = os.environ.get("MC_STATE_DIR")
    if override:
        d = Path(override)
    else:
        d = Path.home() / ".iterm-mcp" / "mc_reprompt"
    d.mkdir(parents=True, exist_ok=True)

    # Opportunistic prune: remove counter files older than 7 days.
    import time as _time
    cutoff = _time.time() - (7 * 24 * 3600)
    try:
        for f in d.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass

    return d


def _session_state_path(session_id: str) -> Path:
    """Return the reprompt-counter file path for a session.

    Args:
        session_id: The Claude Code session identifier string.

    Returns:
        Path to the counter file under the state directory.
    """
    # Sanitize: keep only alnum, dash, underscore to avoid path traversal.
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', session_id) if session_id else "default"
    return _mc_state_dir() / safe


def _read_reprompt_count(session_id: str) -> int:
    """Read the reprompt counter for a session.

    Args:
        session_id: The Claude Code session identifier string.

    Returns:
        The counter value (0 if absent or corrupt).
    """
    p = _session_state_path(session_id)
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_reprompt_count(session_id: str, count: int) -> None:
    """Write the reprompt counter for a session.

    Args:
        session_id: The Claude Code session identifier string.
        count: The integer value to persist.
    """
    p = _session_state_path(session_id)
    p.write_text(str(count), encoding="utf-8")


def _response_has_options(text: str) -> bool:
    """Return True when text contains at least one numbered option line.

    Uses OPTION_PATTERN so the detector agrees exactly with MC_INSTRUCTION.

    Args:
        text: The assistant turn text to inspect.

    Returns:
        True if a numbered option line is found, False otherwise.
    """
    return bool(OPTION_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Decision-shaping helpers (pure functions, importable for testing)
# ---------------------------------------------------------------------------


def build_stop_decision(answer: dict) -> dict:
    """Shape a stop-hook JSON output from a DriverStore answer dict.

    Args:
        answer: {"choice_id": str, "custom_text": str | None}

    Returns:
        {"decision": "block", "reason": str} or {} (empty = let Claude stop).
    """
    choice_id = answer.get("choice_id", "stop")
    custom_text = answer.get("custom_text")

    if choice_id == "stop":
        return {}

    if choice_id == "custom":
        if custom_text:
            return {"decision": "block", "reason": custom_text}
        return {}

    # Find the matching option's text to use as the reason.
    for opt in STOP_OPTIONS:
        if opt["id"] == choice_id:
            reason = opt["text"]
            if reason:
                return {"decision": "block", "reason": reason}
            return {}

    # Unknown choice — default to stop.
    return {}


def build_pretooluse_decision(answer: dict) -> dict:
    """Shape a pretooluse-hook JSON output from a DriverStore answer dict.

    Args:
        answer: {"choice_id": str, "custom_text": str | None}

    Returns:
        hookSpecificOutput envelope with permissionDecision.
    """
    choice_id = answer.get("choice_id", "allow")

    # Map choice_id to permission decision text.
    decision = choice_id if choice_id in ("allow", "deny", "ask") else "allow"

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": decision,
        }
    }


def build_userpromptsubmit_decision(mc_on: bool) -> dict:
    """Shape a UserPromptSubmit hook JSON output.

    Args:
        mc_on: Whether MC coercion is currently enabled.

    Returns:
        hookSpecificOutput envelope, with additionalContext when mc_on is True.
    """
    if mc_on:
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": MC_INSTRUCTION,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
        }
    }


# ---------------------------------------------------------------------------
# Transcript reading (inlined from ControIDE to avoid cross-repo import)
# ---------------------------------------------------------------------------


def _read_last_assistant_text(transcript_path: str, max_chars: int | None = 500) -> str:
    """Read the last assistant message text from a Claude Code JSONL transcript.

    Args:
        transcript_path: Absolute path to the JSONL transcript file.
        max_chars: Maximum characters to return from the message, or None
            for no truncation (full text).  Defaults to 500 for the #130
            dashboard preview; pass None at the MC detection call site so
            options at the end of long responses are not missed.

    Returns:
        Up to max_chars characters of the last assistant message (or the
        full text when max_chars is None), or an empty string if the file
        cannot be read or has no assistant messages.
    """
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""

    last_text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        # Content is either a string or a list of blocks.
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, str):
            last_text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            last_text = " ".join(parts)

    return last_text if max_chars is None else last_text[:max_chars]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post_ask(payload: dict) -> dict:
    """POST to /api/ask and block until answered.

    Args:
        payload: Question payload (hook_type, prompt, options, timeout).

    Returns:
        Answer dict {"choice_id": str, "choice_text": str, "custom_text": ...}.

    Raises:
        urllib.error.URLError: If the dashboard is not reachable.
        RuntimeError: If the server returns a non-200 status.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{DASHBOARD_URL}/api/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # socket timeout > HOOK_TIMEOUT so the server can time out first
    with urllib.request.urlopen(req, timeout=HOOK_TIMEOUT + 30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"/api/ask returned HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Hook entry points
# ---------------------------------------------------------------------------


def run_userpromptsubmit_hook(stdin_data: dict) -> None:
    """Execute the UserPromptSubmit hook logic.

    When MC coercion is ON: resets the per-session reprompt counter (new user
    turn) and emits additionalContext instructing Claude to end its response
    with a numbered multiple-choice list.

    When MC coercion is OFF: emits the minimal no-op JSON immediately.

    Args:
        stdin_data: Parsed JSON from Claude Code's UserPromptSubmit hook stdin.
    """
    mc_on = _mc_flag_on()

    if mc_on:
        # Reset per-session reprompt counter — new user turn begins.
        session_id = stdin_data.get("session_id", "")
        _write_reprompt_count(session_id, 0)

    decision = build_userpromptsubmit_decision(mc_on)
    print(json.dumps(decision))


def run_stop_hook(stdin_data: dict) -> None:
    """Execute the Stop hook logic.

    When MC coercion is ON:
        1. Reads the last assistant turn from the transcript.
        2. If options are missing AND reprompt count < 1: blocks with a
           reprompt instruction and increments the counter.
        3. Otherwise: falls through to the existing #130 dashboard behavior.
           (Phase-2 seam: when count==1 and options still missing, a future
           synthesizer would generate options here before posting to dashboard.)

    When MC coercion is OFF: proceeds directly to the #130 dashboard behavior.

    Args:
        stdin_data: Parsed JSON from Claude Code's Stop hook stdin.
    """
    if _mc_flag_on():
        transcript_path = stdin_data.get("transcript_path", "")
        session_id = stdin_data.get("session_id", "")

        # Use max_chars=None to scan the FULL assistant text — options may
        # appear well past char 500 in long responses.
        last_text = _read_last_assistant_text(transcript_path, max_chars=None) if transcript_path else ""
        reprompt_count = _read_reprompt_count(session_id)

        if not _response_has_options(last_text) and reprompt_count < 1:
            # Block once and ask Claude to redo with numbered options.
            _write_reprompt_count(session_id, 1)
            print(json.dumps({"decision": "block", "reason": MC_REPROMPT_REASON}))
            return

        # Options present, or we already reprompted once — fall through.
        # TODO Phase-2: when reprompt_count == 1 and options still missing,
        # synthesize a choices list from `last_text` here (e.g. call a local
        # LLM or regex-extract bullet points) and inject it into `prompt`
        # before posting to the dashboard, so operators get sensible tiles
        # even when Claude ignores the format after being asked once.

    # --- Existing #130 stop behavior (unchanged) ---
    transcript_path = stdin_data.get("transcript_path", "")
    prompt = _read_last_assistant_text(transcript_path) if transcript_path else ""
    if not prompt:
        prompt = "(No transcript text available)"

    try:
        answer = _post_ask({
            "hook_type": "stop",
            "prompt": prompt,
            "options": STOP_OPTIONS,
            "timeout": HOOK_TIMEOUT,
        })
        decision = build_stop_decision(answer)
    except Exception as exc:
        print(f"[driver_hook] Stop hook error, falling back to {{}}: {exc}", file=sys.stderr)
        decision = {}

    print(json.dumps(decision))


def run_pretooluse_hook(stdin_data: dict) -> None:
    """Execute the PreToolUse hook logic.

    Args:
        stdin_data: Parsed JSON from Claude Code's PreToolUse hook stdin.
    """
    tool_name = stdin_data.get("tool_name", "unknown")
    tool_input = stdin_data.get("tool_input", {})
    input_preview = json.dumps(tool_input, indent=2)[:400]
    prompt = f"Tool: {tool_name}\nInput: {input_preview}"

    try:
        answer = _post_ask({
            "hook_type": "pretooluse",
            "prompt": prompt,
            "options": PRETOOLUSE_OPTIONS,
            "timeout": HOOK_TIMEOUT,
        })
        decision = build_pretooluse_decision(answer)
    except Exception as exc:
        print(
            f"[driver_hook] PreToolUse hook error, falling back to allow: {exc}",
            file=sys.stderr,
        )
        decision = build_pretooluse_decision({"choice_id": "allow"})

    print(json.dumps(decision))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point for hook scripts.

    Reads hook type from argv[1], parses stdin JSON, dispatches to the
    appropriate run_*_hook function.
    """
    valid_modes = ("stop", "pretooluse", "userpromptsubmit")
    if len(sys.argv) < 2 or sys.argv[1] not in valid_modes:
        print(f"Usage: driver_hook.py {'|'.join(valid_modes)}", file=sys.stderr)
        sys.exit(1)

    hook_type = sys.argv[1]

    try:
        raw = sys.stdin.read()
        stdin_data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"[driver_hook] Failed to parse stdin JSON: {exc}", file=sys.stderr)
        stdin_data = {}

    if hook_type == "stop":
        run_stop_hook(stdin_data)
    elif hook_type == "pretooluse":
        run_pretooluse_hook(stdin_data)
    else:
        run_userpromptsubmit_hook(stdin_data)


if __name__ == "__main__":
    main()
