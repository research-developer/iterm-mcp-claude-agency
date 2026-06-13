#!/usr/bin/env python3
"""
ControIDE Phase-0 driver hook helper.

Called by stop.sh and pretooluse.sh. Reads Claude Code hook JSON from stdin,
POSTs a question to the dashboard server, blocks until answered, then emits
the structured hook-decision JSON to stdout.

Usage:
    driver_hook.py stop        # Stop hook
    driver_hook.py pretooluse  # PreToolUse hook

Error handling:
    If the dashboard is not running or /api/ask times out, the hook falls
    back to a safe default and does NOT crash Claude Code:
    - Stop hook fallback: emit {} (let Claude stop naturally)
    - PreToolUse hook fallback: emit allow (permissive default)
    Errors are logged to stderr so they appear in Claude Code's hook logs.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

DASHBOARD_URL = "http://127.0.0.1:9999"
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


# ---------------------------------------------------------------------------
# Transcript reading (inlined from ControIDE to avoid cross-repo import)
# ---------------------------------------------------------------------------


def _read_last_assistant_text(transcript_path: str, max_chars: int = 500) -> str:
    """Read the last assistant message text from a Claude Code JSONL transcript.

    Args:
        transcript_path: Absolute path to the JSONL transcript file.
        max_chars: Maximum characters to return from the message.

    Returns:
        First max_chars characters of the last assistant message, or an
        empty string if the file cannot be read or has no assistant messages.
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

    return last_text[:max_chars]


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


def run_stop_hook(stdin_data: dict) -> None:
    """Execute the Stop hook logic.

    Args:
        stdin_data: Parsed JSON from Claude Code's Stop hook stdin.
    """
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
    if len(sys.argv) < 2 or sys.argv[1] not in ("stop", "pretooluse"):
        print("Usage: driver_hook.py stop | pretooluse", file=sys.stderr)
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
    else:
        run_pretooluse_hook(stdin_data)


if __name__ == "__main__":
    main()
