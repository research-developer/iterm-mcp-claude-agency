"""Text-to-speech: supertonic (preferred) -> macOS say, with optional
output-device routing (e.g. a Bluetooth headset) via VOICE_OUTPUT_DEVICE.

Default: playback to the system default output (`supertonic say` / `say`).

When VOICE_OUTPUT_DEVICE names an output device (case-insensitive substring of
the device name), `speak()` synthesizes to a wav with supertonic and plays it to
THAT device with sounddevice — so the agent can be "in your ear" on a headset
without changing the system default or disturbing others. The capture cue
(`play_cue`) routes the same way, so the "listening" beep isn't on the room
speakers either.

Selection is by name, not a fixed index, so a Bluetooth device's index drifting
across reconnects is a non-issue. If routing is unavailable for any reason
(sounddevice/numpy missing, device not found, synth or playback error) we warn
on stderr and fall back to the default output rather than going silent — a dead
spoken cue should never be fully covert.
"""
import os
import shutil
import subprocess
import sys
import wave
from typing import List, Optional, Tuple

try:  # optional — only needed to route audio to a non-default output device
    import numpy as _np
    import sounddevice as _sd
except Exception:  # pragma: no cover - import guard (missing portaudio/numpy)
    _np = None
    _sd = None

TTS_WAV_PATH = "/tmp/iterm_mcp_voice_out.wav"
CUE_PATH = "/System/Library/Sounds/Ping.aiff"
CUE_RATE = 16000


def _output_device() -> Optional[str]:
    """The configured output-device name (VOICE_OUTPUT_DEVICE), or None."""
    name = os.environ.get("VOICE_OUTPUT_DEVICE", "").strip()
    return name or None


def list_devices() -> List[dict]:
    """Normalized audio devices for `voice devices`: [{index, name, input, output}].

    Empty list if sounddevice is unavailable (so the CLI can print install hints).
    """
    if _sd is None:
        return []
    devices: List[dict] = []
    for idx, dev in enumerate(_sd.query_devices()):
        devices.append({
            "index": idx,
            "name": dev["name"],
            "input": dev.get("max_input_channels", 0) > 0,
            "output": dev.get("max_output_channels", 0) > 0,
        })
    return devices


def speak(text: str, voice: Optional[str] = None) -> None:
    """Speak text. Routes to VOICE_OUTPUT_DEVICE when set, else system default."""
    device = _output_device()
    if device and _speak_to_device(text, device, voice):
        return
    _speak_default(text, voice)


def _speak_default(text: str, voice: Optional[str]) -> None:
    """Speak through the system default output (supertonic, else macOS say)."""
    if shutil.which("supertonic"):
        cmd: List[str] = ["supertonic", "say", text]
        if voice:
            cmd += ["--voice", voice]
    else:
        cmd = ["say", text]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("voice: TTS backend {!r} failed (exit {})".format(
            cmd[0], result.returncode), file=sys.stderr)


def _speak_to_device(text: str, device: str, voice: Optional[str]) -> bool:
    """Synthesize to a wav and play it to the named output device.

    Returns True on success; False if routing is unavailable (the caller then
    falls back to the default output). Never raises and never goes silent.
    """
    if _sd is None or _np is None:
        print("voice: sounddevice/numpy not installed — `pip install sounddevice numpy` "
              "to route audio to {!r}. Using default output.".format(device),
              file=sys.stderr)
        return False
    index = _resolve_output_device(device)
    if index is None:
        print("voice: output device {!r} not found — using default output "
              "(run `voice devices` to list names).".format(device), file=sys.stderr)
        return False
    if not _synthesize(text, TTS_WAV_PATH, voice):
        return False
    try:
        data, rate = _read_wav(TTS_WAV_PATH)
        _sd.play(data, rate, device=index)
        _sd.wait()
    except Exception as exc:  # playback failure -> fall back, don't crash
        print("voice: playback to {!r} failed ({}) — using default output."
              .format(device, exc), file=sys.stderr)
        return False
    finally:
        _cleanup_wav()
    return True


def _resolve_output_device(name: str) -> Optional[int]:
    """Index of the first OUTPUT device whose name contains `name` (case-insensitive).

    Output-only (max_output_channels > 0) so a headset's *microphone* entry never
    shadows its speaker. Name match (not a fixed index) tolerates Bluetooth
    reconnect drift. Returns None if nothing matches.
    """
    want = name.lower()
    for idx, dev in enumerate(_sd.query_devices()):
        if dev.get("max_output_channels", 0) > 0 and want in dev["name"].lower():
            return idx
    return None


def _synthesize(text: str, wav_path: str, voice: Optional[str]) -> bool:
    """supertonic tts -> wav. Only supertonic can synth to a file for routing;
    macOS `say` cannot target a device, so without supertonic we fall back."""
    if not shutil.which("supertonic"):
        print("voice: supertonic not found — device routing needs it (`say` cannot "
              "target a device). Using default output.", file=sys.stderr)
        return False
    cmd = ["supertonic", "tts", text, "-o", wav_path]
    if voice:
        cmd += ["--voice", voice]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("voice: supertonic synth failed (exit {}).".format(result.returncode),
              file=sys.stderr)
        return False
    return True


def _read_wav(path: str) -> Tuple["_np.ndarray", int]:
    """Read a PCM wav into an int16 numpy array + sample rate."""
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    data = _np.frombuffer(frames, dtype=_np.int16)
    if channels > 1:
        data = data.reshape(-1, channels)
    return data, rate


def _cleanup_wav() -> None:
    try:
        os.remove(TTS_WAV_PATH)
    except FileNotFoundError:
        pass


def play_cue() -> bool:
    """Play the capture cue. Routes to VOICE_OUTPUT_DEVICE when set (so the
    'listening' beep is in-ear, not on the room speakers); else afplay default.

    Returns True if a cue played, False on failure (caller may warn).
    """
    device = _output_device()
    if device and _sd is not None and _np is not None:
        index = _resolve_output_device(device)
        if index is not None:
            try:
                _sd.play(_cue_tone(), CUE_RATE, device=index)
                _sd.wait()
                return True
            except Exception:
                pass  # fall through to afplay on the default output
    result = subprocess.run(["afplay", CUE_PATH], check=False)
    return result.returncode == 0


def _cue_tone() -> "_np.ndarray":
    """A short 880 Hz blip as an int16 mono array (for device-routed cues)."""
    n = int(CUE_RATE * 0.12)
    t = _np.arange(n)
    tone = 0.2 * _np.sin(2 * _np.pi * 880.0 * t / CUE_RATE)
    return (tone * 32767).astype(_np.int16)
