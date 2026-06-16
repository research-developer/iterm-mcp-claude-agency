"""Thin voice CLI: arm/disarm/status/say/menu/listen.

`menu` is the core primitive: assert armed -> speak+show options -> beep ->
record -> transcribe -> classify -> print one JSON Action. The agent reads
that JSON and owns every downstream decision.

Every code path that the agent consumes (`menu`, `listen`) prints exactly one
JSON Action — including failures — so a missing backend or malformed input is
a structured `error`/`refused`, never a raw traceback the agent cannot parse.
"""
import argparse
import json
import subprocess
import sys
from typing import List

from core.voice import capture, session, stt, tts
from core.voice.match import classify
from core.voice.models import Action, Option


def _beep() -> None:
    result = subprocess.run(
        ["afplay", "/System/Library/Sounds/Ping.aiff"], check=False)
    if result.returncode != 0:
        # The beep is the audible consent cue; if it fails the capture is no
        # longer fully announced, so make that visible rather than silent.
        print("voice: beep failed (afplay exit {})".format(result.returncode),
              file=sys.stderr)


def _parse_options(raw: str) -> List[Option]:
    return [Option(id=o["id"], label=o["label"], say=o.get("say"))
            for o in json.loads(raw)]


def _emit(action: Action) -> None:
    print(json.dumps(action.to_dict()))


def cmd_arm(args: argparse.Namespace) -> None:
    session.arm(timeout_s=args.timeout)
    print("voice armed ({}s idle timeout)".format(args.timeout))


def cmd_disarm(args: argparse.Namespace) -> None:
    session.disarm()
    print("voice disarmed")


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(session.status(), indent=2))


def cmd_say(args: argparse.Namespace) -> None:
    tts.speak(args.text, voice=args.voice)


def _capture_transcript(mode: str) -> str:
    """Record + transcribe, always clearing the wav. Raises RuntimeError on
    backend failure so callers can emit a typed `error` rather than crash."""
    try:
        wav = capture.record(mode=mode)
        return stt.transcribe(wav)
    finally:
        capture.cleanup()


def cmd_menu(args: argparse.Namespace) -> None:
    if not session.is_armed():
        _emit(Action("refused", value="disarmed"))
        return
    try:
        options = _parse_options(args.options)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        _emit(Action("refused", value="bad-options: {}".format(exc)))
        return
    spoken = (args.prompt + ". ") if args.prompt else ""
    spoken += "; ".join(
        "{}. {}".format(i + 1, o.spoken) for i, o in enumerate(options)
    )
    print("🎙 " + spoken, file=sys.stderr)
    tts.speak(spoken)
    _beep()
    try:
        transcript = _capture_transcript(args.mode)
    except RuntimeError as exc:
        _emit(Action("error", value=str(exc)))
        return
    session.touch()
    _emit(classify(transcript, options))


def cmd_listen(args: argparse.Namespace) -> None:
    if not session.is_armed():
        _emit(Action("refused", value="disarmed"))
        return
    print("🎙 listening…", file=sys.stderr)
    _beep()
    try:
        transcript = _capture_transcript(args.mode)
    except RuntimeError as exc:
        _emit(Action("error", value=str(exc)))
        return
    session.touch()
    print(transcript)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice", description="ControIDE voice layer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arm = sub.add_parser("arm", help="permit capture (idle auto-disarm)")
    p_arm.add_argument("--timeout", type=int, default=600, help="idle seconds")
    p_arm.set_defaults(func=cmd_arm)

    sub.add_parser("disarm", help="forbid capture").set_defaults(func=cmd_disarm)
    sub.add_parser("status", help="show arm state").set_defaults(func=cmd_status)

    p_say = sub.add_parser("say", help="speak text")
    p_say.add_argument("text")
    p_say.add_argument("--voice", default=None)
    p_say.set_defaults(func=cmd_say)

    p_menu = sub.add_parser("menu", help="present options, capture a choice")
    p_menu.add_argument("--options", required=True, help="JSON list of {id,label,say?}")
    p_menu.add_argument("--prompt", default=None)
    p_menu.add_argument("--mode", choices=["vad", "ptt"], default="vad")
    p_menu.set_defaults(func=cmd_menu)

    p_listen = sub.add_parser("listen", help="free-form transcribe")
    p_listen.add_argument("--mode", choices=["vad", "ptt"], default="vad")
    p_listen.set_defaults(func=cmd_listen)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
