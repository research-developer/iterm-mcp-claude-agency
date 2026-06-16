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
