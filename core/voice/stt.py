"""Speech-to-text via whisper.cpp (whisper-cli + ggml-base.en).

A non-zero whisper exit (missing model, corrupt wav, bad args) raises rather
than returning an empty string, so a broken backend can never masquerade as
"the user said nothing" downstream.
"""
import os
import shutil
import subprocess
from pathlib import Path

MODEL_PATH = str(Path("~/.cache/whisper/ggml-base.en.bin").expanduser())


def transcribe(wav_path: str) -> str:
    if not shutil.which("whisper-cli"):
        raise RuntimeError(
            "whisper-cli not found — install with: brew install whisper-cpp"
        )
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(
            "whisper model not found at {} — download ggml-base.en.bin "
            "there (see README)".format(MODEL_PATH)
        )
    result = subprocess.run(
        ["whisper-cli", "-m", MODEL_PATH, "-f", wav_path, "-l", "en", "-nt"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "whisper-cli failed (exit {}): {}".format(
                result.returncode, result.stderr.strip())
        )
    return " ".join(result.stdout.split()).strip()
