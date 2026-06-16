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
