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
