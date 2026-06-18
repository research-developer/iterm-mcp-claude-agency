"""Tests for backend command construction + error handling (no real audio/models)."""
import os
import unittest
from unittest import mock

from core.voice import capture, stt, tts


def _ok(returncode=0, stdout=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


class TestTTS(unittest.TestCase):
    def test_prefers_supertonic(self):
        with mock.patch("core.voice.tts.shutil.which", return_value="/x/supertonic"), \
             mock.patch("core.voice.tts.subprocess.run", return_value=_ok()) as run:
            tts.speak("hello")
        self.assertEqual(run.call_args[0][0][:2], ["supertonic", "say"])

    def test_falls_back_to_say(self):
        with mock.patch("core.voice.tts.shutil.which", return_value=None), \
             mock.patch("core.voice.tts.subprocess.run", return_value=_ok()) as run:
            tts.speak("hello")
        self.assertEqual(run.call_args[0][0][0], "say")

    def test_nonzero_warns_on_stderr(self):
        with mock.patch("core.voice.tts.shutil.which", return_value=None), \
             mock.patch("core.voice.tts.subprocess.run", return_value=_ok(returncode=1)), \
             mock.patch("core.voice.tts.print") as warn:
            tts.speak("hello")
        self.assertTrue(warn.called)


class TestCapture(unittest.TestCase):
    def test_vad_uses_rec_with_silence(self):
        with mock.patch("core.voice.capture.shutil.which", return_value="/x/rec"), \
             mock.patch("core.voice.capture.os.remove"), \
             mock.patch("core.voice.capture.subprocess.run", return_value=_ok()) as run:
            path = capture.record(mode="vad", max_secs=10)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "rec")
        self.assertIn("silence", cmd)
        self.assertEqual(path, capture.WAV_PATH)

    def test_vad_clears_stale_wav_before_recording(self):
        with mock.patch("core.voice.capture.shutil.which", return_value="/x/rec"), \
             mock.patch("core.voice.capture.os.remove") as rm, \
             mock.patch("core.voice.capture.subprocess.run", return_value=_ok()):
            capture.record(mode="vad")
        rm.assert_called_once_with(capture.WAV_PATH)

    def test_vad_nonzero_raises(self):
        with mock.patch("core.voice.capture.shutil.which", return_value="/x/rec"), \
             mock.patch("core.voice.capture.os.remove"), \
             mock.patch("core.voice.capture.subprocess.run", return_value=_ok(returncode=1)):
            with self.assertRaises(RuntimeError):
                capture.record(mode="vad")

    def test_vad_missing_sox_raises(self):
        with mock.patch("core.voice.capture.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                capture.record(mode="vad")

    def test_vad_device_override(self):
        with mock.patch.dict("os.environ", {"VOICE_VAD_DEVICE": "Studio Mic"}):
            self.assertEqual(capture._vad_device(), "Studio Mic")

    def test_ptt_missing_ffmpeg_raises(self):
        with mock.patch("core.voice.capture.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                capture.record(mode="ptt")

    def test_cleanup_removes_wav(self):
        with mock.patch("core.voice.capture.os.remove") as rm:
            capture.cleanup()
        rm.assert_called_once_with(capture.WAV_PATH)

    def test_cleanup_ignores_missing_file(self):
        with mock.patch("core.voice.capture.os.remove", side_effect=FileNotFoundError):
            capture.cleanup()  # must not raise


class TestSTT(unittest.TestCase):
    def test_transcribe_builds_whisper_cmd_and_cleans(self):
        completed = _ok(stdout="  Looks good\n  to me \n")
        with mock.patch("core.voice.stt.shutil.which", return_value="/x/whisper-cli"), \
             mock.patch("core.voice.stt.os.path.exists", return_value=True), \
             mock.patch("core.voice.stt.subprocess.run", return_value=completed) as run:
            text = stt.transcribe("/tmp/x.wav")
        self.assertEqual(run.call_args[0][0][0], "whisper-cli")
        self.assertEqual(text, "Looks good to me")

    def test_transcribe_missing_whisper_raises(self):
        with mock.patch("core.voice.stt.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                stt.transcribe("/tmp/x.wav")

    def test_transcribe_missing_model_raises(self):
        with mock.patch("core.voice.stt.shutil.which", return_value="/x/whisper-cli"), \
             mock.patch("core.voice.stt.os.path.exists", return_value=False):
            with self.assertRaises(RuntimeError):
                stt.transcribe("/tmp/x.wav")

    def test_transcribe_nonzero_raises(self):
        failed = _ok(returncode=1)
        failed.stderr = "model load failed"
        with mock.patch("core.voice.stt.shutil.which", return_value="/x/whisper-cli"), \
             mock.patch("core.voice.stt.os.path.exists", return_value=True), \
             mock.patch("core.voice.stt.subprocess.run", return_value=failed):
            with self.assertRaises(RuntimeError):
                stt.transcribe("/tmp/x.wav")


class TestTTSRouting(unittest.TestCase):
    """Output-device routing: VOICE_OUTPUT_DEVICE -> play to that device."""

    def test_output_device_reads_env(self):
        with mock.patch.dict(os.environ, {"VOICE_OUTPUT_DEVICE": "AirPods Pro"}):
            self.assertEqual(tts._output_device(), "AirPods Pro")

    def test_output_device_unset_is_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(tts._output_device())

    def test_output_device_blank_is_none(self):
        with mock.patch.dict(os.environ, {"VOICE_OUTPUT_DEVICE": "  "}):
            self.assertIsNone(tts._output_device())

    def test_speak_uses_default_when_no_device(self):
        with mock.patch("core.voice.tts._output_device", return_value=None), \
             mock.patch("core.voice.tts._speak_to_device") as route, \
             mock.patch("core.voice.tts._speak_default") as default:
            tts.speak("hi")
        route.assert_not_called()
        default.assert_called_once()

    def test_speak_routes_when_device_set(self):
        with mock.patch("core.voice.tts._output_device", return_value="AirPods"), \
             mock.patch("core.voice.tts._speak_to_device", return_value=True) as route, \
             mock.patch("core.voice.tts._speak_default") as default:
            tts.speak("hi")
        route.assert_called_once()
        default.assert_not_called()

    def test_speak_falls_back_when_routing_fails(self):
        with mock.patch("core.voice.tts._output_device", return_value="AirPods"), \
             mock.patch("core.voice.tts._speak_to_device", return_value=False), \
             mock.patch("core.voice.tts._speak_default") as default:
            tts.speak("hi")
        default.assert_called_once()

    def test_resolve_output_device_name_match_output_only(self):
        devices = [
            {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Preston AirPods Pro", "max_input_channels": 0, "max_output_channels": 2},
        ]
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = devices
        with mock.patch("core.voice.tts._sd", fake_sd):
            self.assertEqual(tts._resolve_output_device("airpods"), 2)

    def test_resolve_output_device_ignores_input_only_match(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "Fancy Headset Microphone", "max_input_channels": 1, "max_output_channels": 0},
        ]
        with mock.patch("core.voice.tts._sd", fake_sd):
            self.assertIsNone(tts._resolve_output_device("headset"))

    def test_resolve_output_device_none_when_absent(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
        ]
        with mock.patch("core.voice.tts._sd", fake_sd):
            self.assertIsNone(tts._resolve_output_device("airpods"))

    def test_speak_to_device_without_sounddevice_returns_false(self):
        with mock.patch("core.voice.tts._sd", None), \
             mock.patch("core.voice.tts._np", None), \
             mock.patch("core.voice.tts.print"):
            self.assertFalse(tts._speak_to_device("hi", "AirPods", None))

    def test_speak_to_device_device_not_found_returns_false(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = []
        with mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts.print"):
            self.assertFalse(tts._speak_to_device("hi", "AirPods", None))

    def test_speak_to_device_plays_to_resolved_index(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "AirPods", "max_input_channels": 0, "max_output_channels": 2},
        ]
        with mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts._synthesize", return_value=True), \
             mock.patch("core.voice.tts._read_wav", return_value=("DATA", 16000)), \
             mock.patch("core.voice.tts._cleanup_wav"):
            ok = tts._speak_to_device("hi", "AirPods", None)
        self.assertTrue(ok)
        fake_sd.play.assert_called_once_with("DATA", 16000, device=0)
        fake_sd.wait.assert_called_once()

    def test_speak_to_device_falls_back_when_read_raises(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "AirPods", "max_input_channels": 0, "max_output_channels": 2},
        ]
        with mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts._synthesize", return_value=True), \
             mock.patch("core.voice.tts._read_wav", side_effect=ValueError("bad width")), \
             mock.patch("core.voice.tts._cleanup_wav"), \
             mock.patch("core.voice.tts.print"):
            self.assertFalse(tts._speak_to_device("hi", "AirPods", None))

    def test_speak_to_device_falls_back_when_play_raises(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "AirPods", "max_input_channels": 0, "max_output_channels": 2},
        ]
        fake_sd.play.side_effect = RuntimeError("portaudio error")
        with mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts._synthesize", return_value=True), \
             mock.patch("core.voice.tts._read_wav", return_value=("DATA", 16000)), \
             mock.patch("core.voice.tts._cleanup_wav"), \
             mock.patch("core.voice.tts.print"):
            self.assertFalse(tts._speak_to_device("hi", "AirPods", None))


class TestListDevices(unittest.TestCase):
    """tts.list_devices() normalizes sounddevice's table for `voice devices`."""

    def test_empty_without_sounddevice(self):
        with mock.patch("core.voice.tts._sd", None):
            self.assertEqual(tts.list_devices(), [])

    def test_normalizes_query_devices(self):
        fake_sd = mock.Mock()
        fake_sd.query_devices.return_value = [
            {"name": "Mic", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "Spk", "max_input_channels": 0, "max_output_channels": 2},
        ]
        with mock.patch("core.voice.tts._sd", fake_sd):
            devs = tts.list_devices()
        self.assertEqual(devs[0], {"index": 0, "name": "Mic", "input": True, "output": False})
        self.assertEqual(devs[1], {"index": 1, "name": "Spk", "input": False, "output": True})


class TestCue(unittest.TestCase):
    """The capture cue routes to the headset too, so it isn't on room speakers."""

    def test_cue_default_uses_afplay(self):
        with mock.patch("core.voice.tts._output_device", return_value=None), \
             mock.patch("core.voice.tts.subprocess.run", return_value=_ok()) as run:
            tts.play_cue()
        self.assertEqual(run.call_args[0][0][0], "afplay")

    def test_cue_routes_to_device(self):
        fake_sd = mock.Mock()
        with mock.patch("core.voice.tts._output_device", return_value="AirPods"), \
             mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts._resolve_output_device", return_value=3), \
             mock.patch("core.voice.tts._cue_tone", return_value="TONE"), \
             mock.patch("core.voice.tts.subprocess.run") as run:
            ok = tts.play_cue()
        self.assertTrue(ok)
        fake_sd.play.assert_called_once_with("TONE", 16000, device=3)
        run.assert_not_called()

    def test_cue_falls_back_to_afplay_when_device_play_raises(self):
        fake_sd = mock.Mock()
        fake_sd.play.side_effect = RuntimeError("portaudio error")
        with mock.patch("core.voice.tts._output_device", return_value="AirPods"), \
             mock.patch("core.voice.tts._sd", fake_sd), \
             mock.patch("core.voice.tts._np", mock.Mock()), \
             mock.patch("core.voice.tts._resolve_output_device", return_value=3), \
             mock.patch("core.voice.tts._cue_tone", return_value="TONE"), \
             mock.patch("core.voice.tts.print") as warn, \
             mock.patch("core.voice.tts.subprocess.run", return_value=_ok()) as run:
            ok = tts.play_cue()
        self.assertEqual(run.call_args[0][0][0], "afplay")  # fell back, not silent
        self.assertTrue(warn.called)


class TestReadWav(unittest.TestCase):
    """_read_wav handles real PCM wavs and rejects unsupported sample widths."""

    @staticmethod
    def _write_wav(path, channels, sampwidth, rate, frames_bytes):
        import wave
        with wave.open(path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(rate)
            wf.writeframes(frames_bytes)

    def test_reads_mono_int16(self):
        import tempfile
        import numpy as np
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            samples = np.array([0, 100, -100, 32767, -32768], dtype=np.int16)
            self._write_wav(path, 1, 2, 16000, samples.tobytes())
            data, rate = tts._read_wav(path)
            self.assertEqual(rate, 16000)
            self.assertEqual(list(data), list(samples))
        finally:
            os.remove(path)

    def test_reads_stereo_reshapes_to_frames_by_channels(self):
        import tempfile
        import numpy as np
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            samples = np.array([1, 2, 3, 4, 5, 6], dtype=np.int16)  # 3 frames x 2ch
            self._write_wav(path, 2, 2, 16000, samples.tobytes())
            data, _ = tts._read_wav(path)
            self.assertEqual(data.shape, (3, 2))
        finally:
            os.remove(path)

    def test_unsupported_sampwidth_raises(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self._write_wav(path, 1, 3, 16000, b"\x00\x00\x00\x01\x02\x03")  # 24-bit
            with self.assertRaises(ValueError):
                tts._read_wav(path)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
