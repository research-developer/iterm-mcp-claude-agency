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
