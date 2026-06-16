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
