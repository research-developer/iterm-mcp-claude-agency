"""Tests for the voice CLI routing + JSON contract (backends mocked)."""
import contextlib
import json
import unittest
from unittest import mock

from core.voice import cli


@contextlib.contextmanager
def armed_pipeline(transcript="", record_exc=None):
    """Patch the whole armed menu/listen pipeline; backends never touch audio."""
    record = mock.patch("core.voice.capture.record", return_value="/tmp/x.wav")
    if record_exc is not None:
        record = mock.patch("core.voice.capture.record", side_effect=record_exc)
    with mock.patch("core.voice.session.is_armed", return_value=True), \
         mock.patch("core.voice.session.touch"), \
         mock.patch("core.voice.cli._beep"), \
         mock.patch("core.voice.tts.speak"), \
         record, \
         mock.patch("core.voice.capture.cleanup"), \
         mock.patch("core.voice.stt.transcribe", return_value=transcript), \
         mock.patch("builtins.print") as out:
        yield out


def _last_json(out):
    return json.loads(out.call_args[0][0])


class TestVoiceCli(unittest.TestCase):
    def _run(self, argv):
        with mock.patch("sys.argv", ["voice"] + argv):
            cli.main()

    def test_menu_refused_when_disarmed(self):
        with mock.patch("core.voice.session.is_armed", return_value=False), \
             mock.patch("builtins.print") as out:
            self._run(["menu", "--options", '[{"id":"a","label":"A"}]'])
        self.assertEqual(_last_json(out)["action"], "refused")

    def test_menu_runs_pipeline_when_armed(self):
        opts = '[{"id":"a","label":"Apple"},{"id":"b","label":"Banana"}]'
        with armed_pipeline(transcript="banana") as out:
            self._run(["menu", "--options", opts])
        payload = _last_json(out)
        self.assertEqual((payload["action"], payload["value"]), ("select", "b"))

    def test_menu_bad_json_options_refused(self):
        with mock.patch("core.voice.session.is_armed", return_value=True), \
             mock.patch("builtins.print") as out:
            self._run(["menu", "--options", "{not json}"])
        payload = _last_json(out)
        self.assertEqual(payload["action"], "refused")
        self.assertTrue(str(payload["value"]).startswith("bad-options"))

    def test_menu_incomplete_options_refused(self):
        with mock.patch("core.voice.session.is_armed", return_value=True), \
             mock.patch("builtins.print") as out:
            self._run(["menu", "--options", '[{"label":"missing id"}]'])
        self.assertEqual(_last_json(out)["action"], "refused")

    def test_menu_backend_error_is_structured(self):
        opts = '[{"id":"a","label":"Apple"}]'
        with armed_pipeline(record_exc=RuntimeError("sox not found")) as out:
            self._run(["menu", "--options", opts])
        payload = _last_json(out)
        self.assertEqual(payload["action"], "error")
        self.assertIn("sox not found", payload["value"])

    def test_menu_empty_transcript_is_nomatch(self):
        opts = '[{"id":"a","label":"Apple"},{"id":"b","label":"Banana"}]'
        with armed_pipeline(transcript="") as out:
            self._run(["menu", "--options", opts])
        self.assertEqual(_last_json(out)["action"], "nomatch")

    def test_menu_offmenu_transcript_is_freeform(self):
        opts = '[{"id":"a","label":"Apple"},{"id":"b","label":"Banana"}]'
        with armed_pipeline(transcript="my unrelated answer") as out:
            self._run(["menu", "--options", opts])
        self.assertEqual(_last_json(out)["action"], "freeform")

    def test_arm_calls_session_arm(self):
        with mock.patch("core.voice.session.arm") as arm, \
             mock.patch("builtins.print"):
            self._run(["arm", "--timeout", "300"])
        arm.assert_called_once_with(timeout_s=300)

    def test_disarm_routes_to_session(self):
        with mock.patch("core.voice.session.disarm") as disarm, \
             mock.patch("builtins.print"):
            self._run(["disarm"])
        disarm.assert_called_once_with()

    def test_status_routes_to_session(self):
        with mock.patch("core.voice.session.status",
                        return_value={"armed": False, "raw": {}}) as status, \
             mock.patch("builtins.print"):
            self._run(["status"])
        status.assert_called_once_with()

    def test_say_calls_tts(self):
        with mock.patch("core.voice.tts.speak") as speak:
            self._run(["say", "hello there"])
        speak.assert_called_once_with("hello there", voice=None)

    def test_listen_prints_transcript_when_armed(self):
        with armed_pipeline(transcript="open answer") as out:
            self._run(["listen"])
        self.assertIn("open answer", " ".join(str(c) for c in out.call_args_list))

    def test_listen_backend_error_is_structured(self):
        with armed_pipeline(record_exc=RuntimeError("whisper-cli failed")) as out:
            self._run(["listen"])
        self.assertIn("error", " ".join(str(c) for c in out.call_args_list))


class TestVoicePackaging(unittest.TestCase):
    def test_voice_package_importable_as_module_main(self):
        import importlib
        # __main__ must import cleanly so `python -m core.voice` works.
        importlib.import_module("core.voice.__main__")

    def test_pyproject_registers_voice_script_and_package(self):
        from pathlib import Path
        text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text()
        self.assertIn('voice = "core.voice.cli:main"', text)
        self.assertIn('"core.voice"', text)


if __name__ == "__main__":
    unittest.main()
