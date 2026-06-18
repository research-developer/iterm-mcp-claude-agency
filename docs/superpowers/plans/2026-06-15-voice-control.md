# Voice Control (ControIDE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `core/voice/` module + thin `voice` CLI that lets the agent audibly prompt the user (TTS) and capture spoken multiple-choice responses (STT), gated by an arm-once/idle-auto-disarm consent model.

**Architecture:** The voice layer is dumb I/O; the agent is the brain. `voice menu` does present→capture→classify and returns a typed JSON action (`select|repeat|regenerate|drilldown|freeform|nomatch|refused`); the agent owns the option tree and regeneration. Backends are thin local subprocess wrappers (supertonic/say for TTS, sox/ffmpeg for capture, whisper-cli for STT) selected at runtime, so every unit is testable without audio.

**Tech Stack:** Python 3.8-compatible (`typing.Optional/List`, no `X | Y` or `list[X]`); `unittest` + `unittest.mock`; macOS `supertonic`/`say`/`afplay`, `sox` (`rec`), `ffmpeg` (avfoundation), `whisper-cli` (whisper.cpp) + `ggml-base.en`.

**Spec:** `docs/superpowers/specs/2026-06-15-voice-control-design.md` · **Tracking issue:** #138

**Conventions (read before starting):**
- Tests are `unittest.TestCase` classes in `tests/test_*.py`; run a single module with `python -m unittest tests.<module> -v`. Do NOT run the full suite (`unittest discover`) — it hangs on live-iTerm modules.
- All new code must be Python 3.8-compatible: import `from typing import Optional, List, Dict`; never use `str | None` or `list[str]`.
- Backends are invoked via `subprocess`; tests mock `subprocess.run`/`Popen` and `shutil.which` — never touch the real mic/model in a default test run.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/voice/__init__.py` | Package marker; re-exports `Option`, `Action`. |
| `core/voice/models.py` | `Option` and `Action` dataclasses + `Action.to_dict()`. |
| `core/voice/match.py` | `classify(transcript, options) -> Action` — the only "intelligence" in the layer. |
| `core/voice/session.py` | Arm-state machine: `arm/disarm/touch/is_armed/status` over `~/.iterm-mcp/voice/state.json`. |
| `core/voice/tts.py` | `speak(text, voice=None)` — supertonic→say. |
| `core/voice/capture.py` | `record(mode, max_secs) -> wav_path` — sox VAD / ffmpeg PTT. |
| `core/voice/stt.py` | `transcribe(wav_path) -> str` — whisper-cli. |
| `core/voice/cli.py` | argparse dispatch: `arm/disarm/status/say/menu/listen`; `menu` orchestrates the pipeline + emits JSON. |
| `core/voice/__main__.py` | `python -m core.voice` entry → `cli.main()`. |
| `tests/test_voice_models.py` | Option/Action behaviour. |
| `tests/test_voice_match.py` | classifier behaviour (pure). |
| `tests/test_voice_session.py` | arm/idle-expiry state machine (temp state file, mock clock). |
| `tests/test_voice_backends.py` | tts/capture/stt command construction + backend fallback (mock subprocess). |
| `tests/test_voice_cli.py` | CLI routing, JSON contract, refused-when-disarmed (mock backends). |
| `tests/test_voice_live.py` | Opt-in, env-gated real-audio smoke test (skipped by default). |
| `pyproject.toml` | Add `core.voice` package + `voice` console script. |

---

## Task 1: Package scaffold + data models

**Files:**
- Create: `core/voice/__init__.py`
- Create: `core/voice/models.py`
- Test: `tests/test_voice_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_models.py
"""Tests for core.voice data models."""
import unittest

from core.voice.models import Option, Action


class TestOption(unittest.TestCase):
    def test_spoken_defaults_to_label(self):
        self.assertEqual(Option(id="a", label="Clean it up").spoken, "Clean it up")

    def test_spoken_uses_say_when_present(self):
        self.assertEqual(
            Option(id="a", label="Clean it up", say="tidy the logs").spoken,
            "tidy the logs",
        )


class TestAction(unittest.TestCase):
    def test_to_dict_shape(self):
        d = Action("select", transcript="two", value="b", confidence=1.0).to_dict()
        self.assertEqual(
            d, {"action": "select", "value": "b", "transcript": "two", "confidence": 1.0}
        )

    def test_confidence_rounded(self):
        d = Action("freeform", transcript="x", confidence=0.33333).to_dict()
        self.assertEqual(d["confidence"], 0.333)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_models -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/voice/__init__.py
"""Voice interaction layer (ControIDE): TTS prompts + STT multiple-choice capture."""
from core.voice.models import Action, Option

__all__ = ["Action", "Option"]
```

```python
# core/voice/models.py
"""Data models for the voice layer.

Option: a single multiple-choice item the agent presents.
Action: the typed result the voice layer hands back to the agent.
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Option:
    """One multiple-choice option.

    Attributes:
        id: Stable identifier the agent uses to act on a selection.
        label: On-screen text.
        say: Optional spoken phrasing; falls back to ``label``.
    """

    id: str
    label: str
    say: Optional[str] = None

    @property
    def spoken(self) -> str:
        return self.say or self.label


@dataclass
class Action:
    """The voice layer's classified result.

    action is one of:
        select | repeat | regenerate | drilldown | freeform | nomatch | refused
    value carries the option id (select/drilldown), the spoken direction
    (regenerate), or a reason (refused).
    """

    action: str
    transcript: str = ""
    value: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "value": self.value,
            "transcript": self.transcript,
            "confidence": round(self.confidence, 3),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_models -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/voice/__init__.py core/voice/models.py tests/test_voice_models.py
git commit -m "feat(voice): Option/Action data models"
```

---

## Task 2: The classifier (`match.classify`)

**Files:**
- Create: `core/voice/match.py`
- Test: `tests/test_voice_match.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_match.py
"""Tests for the utterance->action classifier."""
import unittest

from core.voice.match import classify
from core.voice.models import Option

OPTS = [
    Option(id="clean", label="Clean it up"),
    Option(id="ship", label="Ship as is"),
    Option(id="revert", label="Revert the change"),
]


class TestClassify(unittest.TestCase):
    def test_empty_is_nomatch(self):
        self.assertEqual(classify("", OPTS).action, "nomatch")
        self.assertEqual(classify("   ", OPTS).action, "nomatch")

    def test_digit_selects(self):
        a = classify("2", OPTS)
        self.assertEqual((a.action, a.value), ("select", "ship"))

    def test_ordinal_word_selects(self):
        self.assertEqual(classify("the first one", OPTS).value, "clean")
        self.assertEqual(classify("option three", OPTS).value, "revert")

    def test_out_of_range_number_is_not_select(self):
        self.assertNotEqual(classify("9", OPTS).action, "select")

    def test_keyword_selects(self):
        a = classify("let's revert the change", OPTS)
        self.assertEqual((a.action, a.value), ("select", "revert"))

    def test_repeat(self):
        self.assertEqual(classify("can you repeat that", OPTS).action, "repeat")
        self.assertEqual(classify("say again", OPTS).action, "repeat")

    def test_regenerate_captures_direction(self):
        a = classify("none of these, something about tests", OPTS)
        self.assertEqual(a.action, "regenerate")
        self.assertIn("tests", a.value)

    def test_drilldown(self):
        a = classify("go deeper on clean it up", OPTS)
        self.assertEqual(a.action, "drilldown")
        self.assertEqual(a.value, "clean")

    def test_freeform_fallback(self):
        a = classify("my name is preston", OPTS)
        self.assertEqual(a.action, "freeform")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_match -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice.match'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/voice/match.py
"""Classify a transcript into a typed Action against the current options.

Resolution order: control phrases (repeat/drilldown/regenerate) ->
leading ordinal -> keyword/fuzzy label match -> freeform/nomatch.
The agent owns everything downstream of this.
"""
import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from core.voice.models import Action, Option

_ORDINALS = {
    "one": 1, "first": 1, "two": 2, "second": 2,
    "three": 3, "third": 3, "four": 4, "fourth": 4,
}
_REPEAT = ("repeat", "say again", "what were they", "what are they")
_REGEN = ("none of these", "something else", "different options",
          "other options", "none")
_DRILL = ("drill down", "go deeper", "deeper", "expand",
          "tell me more", "more on", "more about")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _leading_number(t: str) -> Optional[int]:
    m = re.match(r"(?:option |number |choice )?(\d+)\b", t)
    if m:
        return int(m.group(1))
    for word, n in _ORDINALS.items():
        if re.search(r"\b" + word + r"\b", t):
            return n
    return None


def _best_label(t: str, options: List[Option]) -> Tuple[Optional[str], float]:
    best_id, best = None, 0.0
    for opt in options:
        lab = _norm(opt.label)
        tokens_t, tokens_l = set(t.split()), set(lab.split())
        overlap = len(tokens_t & tokens_l) / max(1, len(tokens_l))
        ratio = SequenceMatcher(None, t, lab).ratio()
        score = max(overlap, ratio)
        if score > best:
            best_id, best = opt.id, score
    return best_id, best


def classify(transcript: str, options: List[Option]) -> Action:
    t = _norm(transcript)
    if not t:
        return Action("nomatch", transcript=transcript)

    if any(p in t for p in _REPEAT):
        return Action("repeat", transcript=transcript, confidence=1.0)

    for p in _DRILL:
        if p in t:
            target, score = _best_label(t.replace(p, " "), options)
            return Action("drilldown", transcript=transcript,
                          value=(target if score >= 0.5 else None), confidence=0.9)

    for p in _REGEN:
        if p in t:
            direction = t.replace(p, " ").strip()
            return Action("regenerate", transcript=transcript,
                          value=(direction or None), confidence=0.9)

    num = _leading_number(t)
    if num is not None and 1 <= num <= len(options):
        return Action("select", transcript=transcript,
                      value=options[num - 1].id, confidence=1.0)

    best_id, score = _best_label(t, options)
    if best_id is not None and score >= 0.6:
        return Action("select", transcript=transcript, value=best_id,
                      confidence=score)

    return Action("freeform", transcript=transcript, confidence=0.3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_match -v`
Expected: PASS (9 tests). If `test_keyword_selects` is brittle, confirm the 0.6 threshold still classifies "revert the change" — it shares 3/3 label tokens so overlap=1.0.

- [ ] **Step 5: Commit**

```bash
git add core/voice/match.py tests/test_voice_match.py
git commit -m "feat(voice): utterance->action classifier"
```

---

## Task 3: Arm-state machine (`session`)

**Files:**
- Create: `core/voice/session.py`
- Test: `tests/test_voice_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_session.py
"""Tests for the voice arm-state machine (no real ~/.iterm-mcp writes)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.voice import session


class TestSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch_path = mock.patch.object(
            session, "STATE_PATH", Path(self._tmp.name) / "state.json"
        )
        self._patch_path.start()
        self._t = [1000.0]
        self._patch_now = mock.patch.object(session, "_now", lambda: self._t[0])
        self._patch_now.start()

    def tearDown(self):
        self._patch_path.stop()
        self._patch_now.stop()
        self._tmp.cleanup()

    def test_disarmed_by_default(self):
        self.assertFalse(session.is_armed())

    def test_arm_then_armed(self):
        session.arm(timeout_s=600)
        self.assertTrue(session.is_armed())

    def test_idle_auto_disarm(self):
        session.arm(timeout_s=600)
        self._t[0] += 601
        self.assertFalse(session.is_armed())

    def test_touch_refreshes_idle(self):
        session.arm(timeout_s=600)
        self._t[0] += 300
        session.touch()
        self._t[0] += 400  # 400 since touch < 600
        self.assertTrue(session.is_armed())

    def test_disarm(self):
        session.arm()
        session.disarm()
        self.assertFalse(session.is_armed())

    def test_status_reports_armed(self):
        session.arm()
        self.assertTrue(session.status()["armed"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_session -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice.session'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/voice/session.py
"""Arm-state machine for voice capture consent.

Capture is permitted only while armed; arming auto-expires after an idle
window so authorization never lingers silently.
"""
import json
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
    except (FileNotFoundError, json.JSONDecodeError):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_session -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/voice/session.py tests/test_voice_session.py
git commit -m "feat(voice): arm-once/idle-auto-disarm state machine"
```

---

## Task 4: Backends — TTS, capture, STT

**Files:**
- Create: `core/voice/tts.py`
- Create: `core/voice/capture.py`
- Create: `core/voice/stt.py`
- Test: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_backends.py
"""Tests for backend command construction (no real audio/models)."""
import unittest
from unittest import mock

from core.voice import capture, stt, tts


class TestTTS(unittest.TestCase):
    def test_prefers_supertonic(self):
        with mock.patch("core.voice.tts.shutil.which", return_value="/x/supertonic"), \
             mock.patch("core.voice.tts.subprocess.run") as run:
            tts.speak("hello")
        self.assertEqual(run.call_args[0][0][:2], ["supertonic", "say"])

    def test_falls_back_to_say(self):
        with mock.patch("core.voice.tts.shutil.which", return_value=None), \
             mock.patch("core.voice.tts.subprocess.run") as run:
            tts.speak("hello")
        self.assertEqual(run.call_args[0][0][0], "say")


class TestCapture(unittest.TestCase):
    def test_vad_uses_rec_with_silence(self):
        with mock.patch("core.voice.capture.shutil.which", return_value="/x/rec"), \
             mock.patch("core.voice.capture.subprocess.run") as run:
            path = capture.record(mode="vad", max_secs=10)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "rec")
        self.assertIn("silence", cmd)
        self.assertEqual(path, capture.WAV_PATH)

    def test_vad_missing_sox_raises(self):
        with mock.patch("core.voice.capture.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                capture.record(mode="vad")

    def test_cleanup_removes_wav(self):
        with mock.patch("core.voice.capture.os.remove") as rm:
            capture.cleanup()
        rm.assert_called_once_with(capture.WAV_PATH)

    def test_cleanup_ignores_missing_file(self):
        with mock.patch("core.voice.capture.os.remove", side_effect=FileNotFoundError):
            capture.cleanup()  # must not raise


class TestSTT(unittest.TestCase):
    def test_transcribe_builds_whisper_cmd_and_cleans(self):
        completed = mock.Mock(stdout="  Looks good\n  to me \n")
        with mock.patch("core.voice.stt.shutil.which", return_value="/x/whisper-cli"), \
             mock.patch("core.voice.stt.subprocess.run", return_value=completed) as run:
            text = stt.transcribe("/tmp/x.wav")
        self.assertEqual(run.call_args[0][0][0], "whisper-cli")
        self.assertEqual(text, "Looks good to me")

    def test_transcribe_missing_whisper_raises(self):
        with mock.patch("core.voice.stt.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                stt.transcribe("/tmp/x.wav")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_backends -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice.tts'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/voice/tts.py
"""Text-to-speech: supertonic (preferred) -> macOS say."""
import shutil
import subprocess
from typing import List, Optional


def speak(text: str, voice: Optional[str] = None) -> None:
    if shutil.which("supertonic"):
        cmd: List[str] = ["supertonic", "say", text]
        if voice:
            cmd += ["--voice", voice]
    else:
        cmd = ["say", text]
    subprocess.run(cmd, check=False)
```

```python
# core/voice/capture.py
"""Microphone capture: sox VAD (record-until-silence) or ffmpeg PTT.

VAD targets the named CoreAudio device via AUDIODEV so it does not pick up
the wrong default input (e.g. a Continuity iPhone mic). PTT records an
avfoundation device index and stops on Enter.
"""
import os
import shutil
import subprocess
from typing import List

WAV_PATH = "/tmp/iterm_mcp_voice_in.wav"
DEVICE_NAME = "MacBook Pro Microphone"   # CoreAudio name used by sox
AVF_DEVICE = "1"                          # ffmpeg avfoundation audio index


def record(mode: str = "vad", max_secs: int = 15) -> str:
    if mode == "ptt":
        return _record_ptt(max_secs)
    return _record_vad(max_secs)


def _record_vad(max_secs: int) -> str:
    if not shutil.which("rec"):
        raise RuntimeError("sox 'rec' not found — install with: brew install sox")
    env = dict(os.environ, AUDIODEV=DEVICE_NAME)
    cmd: List[str] = [
        "rec", "-q", "-c", "1", "-r", "16000", WAV_PATH,
        "silence", "1", "0.1", "3%", "1", "1.5", "3%",
        "trim", "0", str(max_secs),
    ]
    subprocess.run(cmd, check=False, env=env)
    return WAV_PATH


def _record_ptt(max_secs: int) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found — install with: brew install ffmpeg")
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
         "-i", ":" + AVF_DEVICE, "-t", str(max_secs), "-ar", "16000",
         "-ac", "1", "-y", WAV_PATH],
        stdin=subprocess.PIPE,
    )
    try:
        input()  # block until the user presses Enter
    except EOFError:
        pass
    if proc.poll() is None:
        try:
            proc.communicate(b"q", timeout=2)  # ffmpeg quits cleanly on 'q'
        except subprocess.TimeoutExpired:
            proc.terminate()
    return WAV_PATH


def cleanup() -> None:
    """Delete the transient capture wav once it has been consumed."""
    try:
        os.remove(WAV_PATH)
    except FileNotFoundError:
        pass
```

```python
# core/voice/stt.py
"""Speech-to-text via whisper.cpp (whisper-cli + ggml-base.en)."""
import shutil
import subprocess
from pathlib import Path

MODEL_PATH = str(Path("~/.cache/whisper/ggml-base.en.bin").expanduser())


def transcribe(wav_path: str) -> str:
    if not shutil.which("whisper-cli"):
        raise RuntimeError(
            "whisper-cli not found — install with: brew install whisper-cpp"
        )
    result = subprocess.run(
        ["whisper-cli", "-m", MODEL_PATH, "-f", wav_path, "-l", "en", "-nt"],
        capture_output=True, text=True, check=False,
    )
    return " ".join(result.stdout.split()).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_backends -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/voice/tts.py core/voice/capture.py core/voice/stt.py tests/test_voice_backends.py
git commit -m "feat(voice): TTS/capture/STT backend adapters"
```

---

## Task 5: CLI + orchestration (`cli`, `__main__`)

**Files:**
- Create: `core/voice/cli.py`
- Create: `core/voice/__main__.py`
- Test: `tests/test_voice_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_cli.py
"""Tests for the voice CLI routing + JSON contract (backends mocked)."""
import json
import unittest
from unittest import mock

from core.voice import cli


class TestVoiceCli(unittest.TestCase):
    def _run(self, argv):
        with mock.patch("sys.argv", ["voice"] + argv):
            cli.main()

    def test_menu_refused_when_disarmed(self):
        with mock.patch("core.voice.session.is_armed", return_value=False), \
             mock.patch("builtins.print") as out:
            self._run(["menu", "--options", '[{"id":"a","label":"A"}]'])
        payload = json.loads(out.call_args[0][0])
        self.assertEqual(payload["action"], "refused")

    def test_menu_runs_pipeline_when_armed(self):
        opts = '[{"id":"a","label":"Apple"},{"id":"b","label":"Banana"}]'
        with mock.patch("core.voice.session.is_armed", return_value=True), \
             mock.patch("core.voice.session.touch"), \
             mock.patch("core.voice.cli._beep"), \
             mock.patch("core.voice.tts.speak"), \
             mock.patch("core.voice.capture.record", return_value="/tmp/x.wav"), \
             mock.patch("core.voice.capture.cleanup"), \
             mock.patch("core.voice.stt.transcribe", return_value="banana"), \
             mock.patch("builtins.print") as out:
            self._run(["menu", "--options", opts])
        payload = json.loads(out.call_args[0][0])
        self.assertEqual((payload["action"], payload["value"]), ("select", "b"))

    def test_arm_calls_session_arm(self):
        with mock.patch("core.voice.session.arm") as arm, \
             mock.patch("builtins.print"):
            self._run(["arm", "--timeout", "300"])
        arm.assert_called_once_with(timeout_s=300)

    def test_say_calls_tts(self):
        with mock.patch("core.voice.tts.speak") as speak:
            self._run(["say", "hello there"])
        speak.assert_called_once_with("hello there", voice=None)

    def test_listen_prints_transcript_when_armed(self):
        with mock.patch("core.voice.session.is_armed", return_value=True), \
             mock.patch("core.voice.session.touch"), \
             mock.patch("core.voice.cli._beep"), \
             mock.patch("core.voice.capture.record", return_value="/tmp/x.wav"), \
             mock.patch("core.voice.capture.cleanup"), \
             mock.patch("core.voice.stt.transcribe", return_value="open answer"), \
             mock.patch("builtins.print") as out:
            self._run(["listen"])
        self.assertIn("open answer", " ".join(str(c) for c in out.call_args_list))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.voice.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/voice/cli.py
"""Thin voice CLI: arm/disarm/status/say/menu/listen.

`menu` is the core primitive: assert armed -> speak+show options -> beep ->
record -> transcribe -> classify -> print one JSON Action. The agent reads
that JSON and owns every downstream decision.
"""
import argparse
import json
import subprocess
import sys
from typing import List

from core.voice import capture, session, stt, tts
from core.voice.match import classify
from core.voice.models import Action, Option


def _beep() -> None:
    subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], check=False)


def _parse_options(raw: str) -> List[Option]:
    return [Option(id=o["id"], label=o["label"], say=o.get("say"))
            for o in json.loads(raw)]


def _emit(action: Action) -> None:
    print(json.dumps(action.to_dict()))


def cmd_arm(args: argparse.Namespace) -> None:
    session.arm(timeout_s=args.timeout)
    print("voice armed ({}s idle timeout)".format(args.timeout))


def cmd_disarm(args: argparse.Namespace) -> None:
    session.disarm()
    print("voice disarmed")


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(session.status(), indent=2))


def cmd_say(args: argparse.Namespace) -> None:
    tts.speak(args.text, voice=args.voice)


def cmd_menu(args: argparse.Namespace) -> None:
    if not session.is_armed():
        _emit(Action("refused", value="disarmed"))
        return
    options = _parse_options(args.options)
    spoken = (args.prompt + ". ") if args.prompt else ""
    spoken += "; ".join(
        "{}. {}".format(i + 1, o.spoken) for i, o in enumerate(options)
    )
    print("🎙 " + spoken, file=sys.stderr)
    tts.speak(spoken)
    _beep()
    wav = capture.record(mode=args.mode)
    transcript = stt.transcribe(wav)
    capture.cleanup()
    session.touch()
    _emit(classify(transcript, options))


def cmd_listen(args: argparse.Namespace) -> None:
    if not session.is_armed():
        _emit(Action("refused", value="disarmed"))
        return
    print("🎙 listening…", file=sys.stderr)
    _beep()
    wav = capture.record(mode=args.mode)
    transcript = stt.transcribe(wav)
    capture.cleanup()
    session.touch()
    print(transcript)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice", description="ControIDE voice layer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arm = sub.add_parser("arm", help="permit capture (idle auto-disarm)")
    p_arm.add_argument("--timeout", type=int, default=600, help="idle seconds")
    p_arm.set_defaults(func=cmd_arm)

    sub.add_parser("disarm", help="forbid capture").set_defaults(func=cmd_disarm)
    sub.add_parser("status", help="show arm state").set_defaults(func=cmd_status)

    p_say = sub.add_parser("say", help="speak text")
    p_say.add_argument("text")
    p_say.add_argument("--voice", default=None)
    p_say.set_defaults(func=cmd_say)

    p_menu = sub.add_parser("menu", help="present options, capture a choice")
    p_menu.add_argument("--options", required=True, help="JSON list of {id,label,say?}")
    p_menu.add_argument("--prompt", default=None)
    p_menu.add_argument("--mode", choices=["vad", "ptt"], default="vad")
    p_menu.set_defaults(func=cmd_menu)

    p_listen = sub.add_parser("listen", help="free-form transcribe")
    p_listen.add_argument("--mode", choices=["vad", "ptt"], default="vad")
    p_listen.set_defaults(func=cmd_listen)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

```python
# core/voice/__main__.py
"""Entry point for `python -m core.voice`."""
from core.voice.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_cli -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/voice/cli.py core/voice/__main__.py tests/test_voice_cli.py
git commit -m "feat(voice): CLI + menu/listen orchestration with JSON contract"
```

---

## Task 6: Packaging + dependency wiring

**Files:**
- Modify: `pyproject.toml:38-43` (`[project.scripts]` and `[tool.setuptools] packages`)
- Test: `tests/test_voice_cli.py` (extend with a packaging assertion)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_voice_cli.py` (new class):

```python
class TestVoicePackaging(unittest.TestCase):
    def test_voice_package_importable_as_module_main(self):
        import importlib
        # __main__ must import cleanly so `python -m core.voice` works.
        importlib.import_module("core.voice.__main__")

    def test_pyproject_registers_voice_script_and_package(self):
        from pathlib import Path
        text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text()
        self.assertIn('voice = "core.voice.cli:main"', text)
        self.assertIn('"core.voice"', text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_voice_cli.TestVoicePackaging -v`
Expected: FAIL — assertion error (`voice = ...` not in pyproject; `core.voice` not in packages)

- [ ] **Step 3: Make the change**

In `pyproject.toml`, change:

```toml
[project.scripts]
iterm-mcp = "iterm_mcpy.main:main"
```

to:

```toml
[project.scripts]
iterm-mcp = "iterm_mcpy.main:main"
voice = "core.voice.cli:main"
```

and change:

```toml
packages = ["core", "iterm_mcpy", "iterm_mcpy.tools", "utils"]
```

to:

```toml
packages = ["core", "core.voice", "iterm_mcpy", "iterm_mcpy.tools", "utils"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_voice_cli.TestVoicePackaging -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_voice_cli.py
git commit -m "build(voice): register core.voice package + voice console script"
```

---

## Task 7: Env-gated live smoke test + README note

**Files:**
- Create: `tests/test_voice_live.py`
- Modify: `README.md` (append a "Voice control (experimental)" section)

- [ ] **Step 1: Write the test (skipped by default)**

```python
# tests/test_voice_live.py
"""Opt-in real-audio smoke test. NEVER runs by default.

Enable with VOICE_TEST_LIVE=1 and speak when prompted. Honors the project's
test-safety rule: no audio capture in a normal test run.
"""
import os
import unittest


@unittest.skipUnless(os.environ.get("VOICE_TEST_LIVE") == "1",
                     "set VOICE_TEST_LIVE=1 to run the live audio smoke test")
class TestVoiceLive(unittest.TestCase):
    def test_round_trip(self):
        from core.voice import capture, stt, tts
        tts.speak("Say: looks good to me, after the beep.")
        wav = capture.record(mode="vad", max_secs=8)
        text = stt.transcribe(wav).lower()
        self.assertIn("looks good", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it SKIPS by default**

Run: `python -m unittest tests.test_voice_live -v`
Expected: `OK (skipped=1)` — no audio, no mic access.

- [ ] **Step 3: Add README note**

Append to `README.md`:

```markdown
## Voice control (experimental)

A local voice layer (`core/voice/`, `voice` CLI) lets the agent speak prompts
and capture spoken multiple-choice answers. All on-device: `supertonic`/`say`
(TTS), `sox`/`ffmpeg` (capture), `whisper-cli` + `ggml-base.en` (STT).

Setup: `brew install sox whisper-cpp` and download the model to
`~/.cache/whisper/ggml-base.en.bin`. Grant the terminal Microphone permission.

Usage: `voice arm` to permit capture (idle auto-disarms after 10 min),
`voice disarm` to stop. The agent drives `voice menu --options '<json>'`
and reads the JSON action. Nothing leaves the machine. Tracking: #138.
```

- [ ] **Step 4: Verify the targeted voice tests pass together**

Run: `python -m unittest tests.test_voice_models tests.test_voice_match tests.test_voice_session tests.test_voice_backends tests.test_voice_cli tests.test_voice_live -v`
Expected: all PASS (live test skipped).

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_live.py README.md
git commit -m "test(voice): env-gated live smoke test + README usage"
```

---

## Final verification

- [ ] Run the full voice test set (NOT `unittest discover`):
  `python -m unittest tests.test_voice_models tests.test_voice_match tests.test_voice_session tests.test_voice_backends tests.test_voice_cli tests.test_voice_live -v`
  Expected: all green, live test skipped.
- [ ] Manual (optional, requires mic + `brew install sox`): `pip install -e . && voice arm && VOICE_TEST_LIVE=1 python -m unittest tests.test_voice_live -v`
- [ ] Confirm no Python 3.9+ typing syntax slipped in: `grep -rn " | None\|: list\[\|: dict\[" core/voice` returns nothing.

---

## Addendum: output-device routing (Bluetooth / non-default output)

Added after the original plan to satisfy the "have you in my ear" requirement — route TTS **and** the capture cue to a chosen output device (e.g. a Bluetooth headset), not just the system default. Implemented via TDD; all headless voice tests green.

### Task A — `tts.py` output-device routing
**Files:** `core/voice/tts.py`, `tests/test_voice_backends.py`
- [x] `_output_device()` reads `VOICE_OUTPUT_DEVICE` (blank → None).
- [x] `speak()` routes via `_speak_to_device()` when set, else `_speak_default()`; any routing failure warns and falls back (never silent).
- [x] `_resolve_output_device(name)` → first OUTPUT device whose name contains `name` (case-insensitive) via `sounddevice.query_devices()`; input-only entries ignored.
- [x] `_synthesize` (`supertonic tts -o`), `_read_wav` (wave + numpy int16), playback via `sounddevice.play(..., device=index)`, `_cleanup_wav`.
- [x] `play_cue()` routes a generated tone to the device when set, else `afplay` default.
- [x] `sounddevice`/`numpy` imported optionally (`_sd`/`_np` None when absent → graceful fallback).

### Task B — `voice devices` + cue wiring
**Files:** `core/voice/cli.py`, `core/voice/tts.py`, `tests/test_voice_cli.py`
- [x] `tts.list_devices()` → `[{index, name, input, output}]` (empty without sounddevice).
- [x] `voice devices` subcommand lists I/O devices, marks the active `VOICE_OUTPUT_DEVICE`.
- [x] `cli._beep()` delegates to `tts.play_cue()` so the cue is in-ear, not on room speakers.

### Task C — packaging + docs
- [x] `[project.optional-dependencies] voice = ["sounddevice", "numpy"]` (`pip install iterm-mcp[voice]`).
- [x] Spec + README updated: output routing, `voice devices`, `VOICE_OUTPUT_DEVICE`/`VOICE_VAD_DEVICE`/`VOICE_PTT_DEVICE`, Bluetooth HFP/A2DP caveat, name-based selection.
