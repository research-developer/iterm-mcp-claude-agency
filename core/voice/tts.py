"""Text-to-speech: supertonic (preferred) -> macOS say.

A non-zero exit is reported on stderr rather than swallowed, so a dead prompt
(e.g. supertonic installed but broken) is at least visible — the spoken half
of the consent cue failing should not be fully covert.
"""
import shutil
import subprocess
import sys
from typing import List, Optional


def speak(text: str, voice: Optional[str] = None) -> None:
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
