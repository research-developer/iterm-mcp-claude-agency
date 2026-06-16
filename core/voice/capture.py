"""Microphone capture: sox VAD (record-until-silence) or ffmpeg PTT.

VAD targets the named CoreAudio device via AUDIODEV so it does not pick up
the wrong default input (e.g. a Continuity iPhone mic). PTT records an
avfoundation device index and stops on Enter. Both devices are overridable
via VOICE_VAD_DEVICE / VOICE_PTT_DEVICE.

Safety: the wav is a single fixed path reused every turn, so a *failed*
recording must never leave a previous turn's audio behind to be transcribed
as a fresh answer. We delete it before recording and raise on a non-zero
backend exit rather than returning a stale/empty file.
"""
import os
import shutil
import subprocess
from typing import List

WAV_PATH = "/tmp/iterm_mcp_voice_in.wav"
DEFAULT_VAD_DEVICE = "MacBook Pro Microphone"   # CoreAudio name (sox)
DEFAULT_PTT_DEVICE = "1"                          # ffmpeg avfoundation index


def _vad_device() -> str:
    return os.environ.get("VOICE_VAD_DEVICE", DEFAULT_VAD_DEVICE)


def _ptt_device() -> str:
    return os.environ.get("VOICE_PTT_DEVICE", DEFAULT_PTT_DEVICE)


def record(mode: str = "vad", max_secs: int = 15) -> str:
    if mode == "ptt":
        return _record_ptt(max_secs)
    return _record_vad(max_secs)


def _record_vad(max_secs: int) -> str:
    if not shutil.which("rec"):
        raise RuntimeError("sox 'rec' not found — install with: brew install sox")
    cleanup()  # never return a previous turn's recording on failure
    env = dict(os.environ, AUDIODEV=_vad_device())
    cmd: List[str] = [
        "rec", "-q", "-c", "1", "-r", "16000", WAV_PATH,
        "silence", "1", "0.1", "3%", "1", "1.5", "3%",
        "trim", "0", str(max_secs),
    ]
    result = subprocess.run(cmd, check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            "sox 'rec' failed (exit {}) — check microphone permission and that "
            "input device {!r} exists".format(result.returncode, _vad_device())
        )
    return WAV_PATH


def _record_ptt(max_secs: int) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found — install with: brew install ffmpeg")
    cleanup()  # never return a previous turn's recording on failure
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
         "-i", ":" + _ptt_device(), "-t", str(max_secs), "-ar", "16000",
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
