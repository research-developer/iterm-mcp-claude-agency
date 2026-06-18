# Voice Control (ControIDE) — Design

**Date:** 2026-06-15
**Status:** Implemented on `feat/voice-control` — incl. Bluetooth / non-default output-device routing
**Tracking issue:** [#138](https://github.com/research-developer/iterm-mcp-claude-agency/issues/138)

## Goal

Give the agent a **voice interaction layer**: it can audibly prompt the user (TTS) and capture a spoken response (STT) **at its discretion, once the user has armed it**. This is the voice arm of the ControIDE vision — operate Claude Code hands-free / via gamepad + dictation, where responses are primarily **multiple-choice** so they can be driven by a controller or dictation.

A proof-of-concept already validates the full loop on the primary machine: `supertonic say` → `ffmpeg` mic capture (device 1 = MacBook Pro Microphone) → `whisper-cli` (ggml-base.en) transcription → text returned to the agent as a tool result. Microphone permission is granted.

## Guiding decisions (from brainstorming)

1. **Interaction = turn-based + armed window.** The agent initiates: speak a prompt, then listen for the reply. Per-use by default, but the user can *arm* a window so the exchange continues without re-confirming each time. **No persistent / always-on mic capture.**
2. **Answers = a multiple-choice option tree.** When the agent needs input it offers 1–4 options (spoken *and* shown). The user replies by number or keyword, plus control verbs: **select / repeat / regenerate-with-direction / drill-down**, with a **free-form** fallback. The agent owns the tree and all (re)generation; the voice layer is a thin present→capture→classify primitive.
3. **Home = `core/voice/` module + a thin `voice` CLI** invoked via Bash, returning JSON. Designed so an MCP tool and the ControIDE web/gamepad layer can wrap the same module later (tracked in #138).
4. **Capture stop = VAD by default + push-to-talk option.** Silence detection ends a turn hands-free; PTT is available for precision/noise and is the natural gamepad button later.
5. **Consent = arm-once + idle auto-disarm.** Arming is an explicit user act; the window stays armed until the user disarms or after an idle timeout (default 10 min). **Every** capture emits a beep + an on-screen `🎙 listening…` cue. Options are presented aloud *and* on-screen. Nothing leaves the machine.

## The core boundary

**The voice layer is dumb I/O; the agent is the brain.** `voice menu` only ever does present → capture → classify, returning a typed action. Regeneration, the option tree, and drill-down live in the agent's reasoning loop. This boundary keeps every unit testable without audio and lets the CLI, a future MCP tool, and a browser front-end all be interchangeable callers of one JSON contract.

## Components — `core/voice/`

### `tts.py` — speech out (with output-device routing)
- `speak(text: str, voice: str | None = None) -> None` — routes to `VOICE_OUTPUT_DEVICE` if set, else the system default.
- `play_cue() -> bool` — the capture beep, routed the same way (so it's in-ear, not on the room speakers).
- `list_devices() -> list[dict]` — normalized `[{index, name, input, output}]` for `voice devices`.
- Default backend: `supertonic say` (cold) → macOS `say` fallback. Warm `supertonic serve` is deferred (#138).
- **Output-device routing:** when `VOICE_OUTPUT_DEVICE` names an output device, `speak` synthesizes to a wav (`supertonic tts -o`) and plays it to that device via **sounddevice** (PortAudio); the cue plays a short generated tone the same way. Selection is by case-insensitive **name** (not a fixed index), so a Bluetooth device's index drift across reconnects is a non-issue. If routing is unavailable (sounddevice/numpy missing, device not found, synth/playback error) it **warns and falls back** to the default output — never silent. `say` cannot target a device, so routing requires supertonic.

### `capture.py` — microphone in
- `record(mode: str = "vad", max_secs: int = 15, device: int = 1) -> str` (returns wav path).
- **VAD mode:** record until end-of-speech using `sox` silence detection (`brew install sox`); a hard `max_secs` cap guarantees it never hangs.
- **PTT mode:** start recording immediately; stop on a keypress (tap-to-stop in v1; true hold-to-talk is deferred to the gamepad layer, #138).
- Records 16 kHz mono (what whisper expects). The wav is a fixed transient path, overwritten each turn.

### `stt.py` — speech to text
- `transcribe(wav_path: str) -> str`
- Backend: `whisper-cli -m <ggml-base.en> -f <wav> -l en -nt` (cold). Warm whisper-server is deferred (#138). Returns the cleaned transcript string.

### `match.py` — the classifier (the voice layer's only "intelligence")
- `classify(transcript: str, options: list[Option]) -> Action`
- `Action = {action, value, transcript, confidence}` where `action ∈ {select, repeat, regenerate, drilldown, freeform, nomatch, refused, error}`.
- Resolution order: explicit control phrases (word-boundary matched) → leading ordinal ("two"/"2"/"the second") → keyword/fuzzy match against option labels **and spoken text** → else `freeform` (non-empty) / `nomatch` (empty). Only a *leading* token may pick an option, so a trailing noun ("the banana one") never selects option 1.
- Control-phrase vocabulary (configurable, with sensible defaults): repeat = {"repeat", "say again", "what were they"}; regenerate = {"none of these", "something else", "different options"} (remaining words captured as `value` = direction); drilldown = {"drill down", "go deeper", "expand", "more on <label>"}.

### `session.py` — arm state & consent
- State file: `~/.iterm-mcp/voice/state.json` → `{armed: bool, last_interaction: ts, idle_timeout_s: int}`.
- `arm(timeout_s=600)`, `disarm()`, `status() -> dict`, `is_armed() -> bool` (false once `now - last_interaction > idle_timeout_s`; each capture refreshes `last_interaction`).
- Enforces the consent model: `voice menu`/`voice listen` refuse when not armed.

## CLI surface — `voice`

A thin wrapper over `core/voice` (console script and/or `python -m core.voice`):

- `voice arm [--timeout 10m]` · `voice disarm` · `voice status`
- `voice devices` — list input/output devices (find the name for `VOICE_OUTPUT_DEVICE`)
- `voice say "<text>" [--voice F1]`
- `voice menu --options '<json>' [--prompt "<intro>"] [--mode vad|ptt]` → prints the JSON `Action`
- `voice listen [--mode vad|ptt]` → prints a free-form transcript

`--options` JSON shape: `[{"id": "a", "label": "Clean it up", "say": "optional spoken phrasing"}, ...]` (1–4 items).

**Device selection (env, mirroring the capture pattern):** `VOICE_OUTPUT_DEVICE` (TTS + cue output; name substring; unset = system default), `VOICE_VAD_DEVICE` (sox mic name; default "MacBook Pro Microphone"), `VOICE_PTT_DEVICE` (ffmpeg avfoundation index; default "1").

## Audio device routing (headset / non-default output)

For a discreet "in your ear" experience the agent's audio can be pinned to a specific device (e.g. a Bluetooth headset) independent of the system default:

- **Input** (already supported): `VOICE_VAD_DEVICE` / `VOICE_PTT_DEVICE` point capture at the headset mic.
- **Output:** `VOICE_OUTPUT_DEVICE` routes both speech and the cue to the headset via sounddevice; the system default output is untouched, so others don't hear it.
- **Selection by name**, resolved through `sounddevice.query_devices()` to a current index each call — robust to Bluetooth reconnect index drift. Output-only match, so a headset's *mic* entry never shadows its speaker.
- **Bluetooth HFP/A2DP caveat:** opening the headset *mic* forces macOS into the hands-free (HFP) profile → output becomes mono ~16 kHz "phone-call" quality. This is fine for a voice assistant (speech; whisper wants 16 kHz mono anyway), but you cannot get hi-fi stereo output while the mic is live. A hardware/OS constraint, not a bug.

## Data flow — the agent-owned loop

```
agent builds 1–4 options
   │
   ▼
voice menu --options <json> --prompt "..."
   │   ├─ is_armed()? ── no ──▶ {action:"refused"} ──▶ agent falls back to on-screen question
   │   yes
   │   ├─ speak(prompt + options)      (supertonic; also printed on-screen)
   │   ├─ beep + "🎙 listening…"
   │   ├─ record(mode)                 (sox VAD / PTT, max cap)
   │   ├─ transcribe(wav)              (whisper)
   │   └─ classify(transcript, options)
   ▼
JSON Action returned to agent:
   select     → proceed with value (or open the option's sub-menu)
   repeat     → re-present the same options
   regenerate → agent builds a new set from `value` (spoken direction) → re-invoke
   drilldown  → agent presents that option's sub-options
   freeform   → agent interprets `transcript`
   nomatch    → agent repeats once, then falls back to on-screen
(every invocation refreshes the idle timer)
```

## Consent / safety

- **Capture only when armed.** `voice arm` is an explicit user act; idle auto-disarm (default 10 min) + hard `voice disarm` / Ctrl-C.
- **Always-on cues:** beep + `🎙 listening…` line on every capture; options spoken *and* shown. Capture is never silent or covert.
- **Local-only:** supertonic (TTS) and whisper (STT) run entirely on-device; no audio or transcript leaves the machine. The transient wav is overwritten each turn and deleted after transcription.
- **Agent contract:** if disarmed, the agent asks on-screen instead of recording — it must check arm state (via the `refused` action) before relying on voice.

## Error handling

| Condition | Behaviour |
|---|---|
| Not armed | `{action:"refused", value:"disarmed"}` → agent falls back to text |
| Malformed/incomplete `--options` | `{action:"refused", value:"bad-options: …"}` — never a traceback |
| Silence / empty capture | `{action:"nomatch", transcript:""}` → repeat once, then fall back |
| Transcript matches no option | `freeform` (agent interprets) or repeat |
| STT/capture backend fails (missing whisper model, sox/ffmpeg error, lost mic permission) | raises → CLI emits `{action:"error", value:"<msg>"}` so the agent can branch — never a silent `nomatch`. The stale wav is cleared before each capture so a failed recording can't replay a prior turn. |
| TTS backend fails | `say` fallback if supertonic absent; non-zero exit warned on stderr |
| Runaway capture | hard `max_secs` cap ends recording |

## Testing (headless — honors the no-live-windows / no-casual-audio rules)

- `match.py`: utterance→action mapping — ordinals, keywords, fuzzy, control phrases, regenerate-direction extraction, drilldown (pure unit tests, no audio).
- `session.py`: arm/disarm/idle-expiry state machine with a mock clock.
- CLI: `voice menu`/`voice listen` JSON contract, refused-when-disarmed, backend selection — with `tts`/`stt`/`capture` mocked (no real mic or model in CI).
- One **opt-in, env-gated** live smoke test exercising real audio (mirrors the existing `ITERM_MCP_TEST_ALLOW_ACTIVE` pattern); never runs by default.

## Non-goals (YAGNI for v1; tracked in #138)

- Warm-engine service management (`supertonic serve` + persistent whisper server).
- MCP-tool graduation and daemon/AppContext integration.
- ControIDE web surface (browser-forwarded options + selection).
- Gamepad/controller input; true hold-to-talk.
- Cross-agent / remote voice; multilingual prompts; always-on / wake-word mode.

## Confirmed tooling (primary machine)

- TTS: `supertonic` (`/opt/homebrew/bin/supertonic`), `say` fallback; default playback `afplay`.
- Output-device routing: `sounddevice` (PortAudio, bundled) + `numpy` — the `[voice]` extra (`pip install iterm-mcp[voice]`); plays the supertonic-synthesized wav / cue tone to the named device.
- Recording: `ffmpeg` (avfoundation), mic = device **1**; `sox` for VAD (record-until-silence).
- STT: `whisper-cli` 1.8.6 (`/opt/homebrew/bin/whisper-cli`) + `~/.cache/whisper/ggml-base.en.bin`.
