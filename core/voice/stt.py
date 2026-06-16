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
