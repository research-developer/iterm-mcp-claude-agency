"""Arm-state machine for voice capture consent.

Capture is permitted only while armed; arming auto-expires after an idle
window so authorization never lingers silently.
"""
import json
import sys
import time
from pathlib import Path
from typing import Dict

STATE_PATH = Path("~/.iterm-mcp/voice/state.json").expanduser()
DEFAULT_TIMEOUT_S = 600


def _now() -> float:
    return time.time()


def _read() -> Dict[str, object]:
    try:
        return json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print("voice: state file {} is corrupt; treating as disarmed".format(
            STATE_PATH), file=sys.stderr)
        return {}


def _write(state: Dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def arm(timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, object]:
    state = {"armed": True, "last_interaction": _now(), "idle_timeout_s": timeout_s}
    _write(state)
    return state


def disarm() -> None:
    _write({"armed": False, "last_interaction": _now(),
            "idle_timeout_s": DEFAULT_TIMEOUT_S})


def touch() -> None:
    state = _read()
    if state.get("armed"):
        state["last_interaction"] = _now()
        _write(state)


def is_armed() -> bool:
    state = _read()
    if not state.get("armed"):
        return False
    elapsed = _now() - float(state.get("last_interaction", 0))
    return elapsed <= float(state.get("idle_timeout_s", DEFAULT_TIMEOUT_S))


def status() -> Dict[str, object]:
    return {"armed": is_armed(), "raw": _read()}
