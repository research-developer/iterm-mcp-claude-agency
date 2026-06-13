"""
Tests for the always-multiple-choice (MC) coercion layer.

Coverage:
- OPTION_PATTERN detector: positive and negative cases including all accepted
  formats (1), 1., (1)) and clear non-matching text.
- MC flag helpers (_mc_flag_on): ON when flag file present, OFF when absent.
- build_userpromptsubmit_decision: correct JSON shape for ON and OFF.
- Per-session reprompt state machine:
    UserPromptSubmit resets counter → first Stop blocks when options missing →
    second Stop passes (falls through to dashboard); Stop does NOT block when
    options are present; OFF flag → never blocks.
- run_userpromptsubmit_hook: emits correct JSON, resets counter when ON.
- run_stop_hook MC layer: blocks once, falls through on second call, falls
  through when options present, no-op when flag OFF.

All tests use a temp directory for the MC state dir (MC_STATE_DIR env var) and
a temp flag file so they never touch ~/.iterm-mcp. No live iTerm2 / Claude
Code required; _post_ask is monkey-patched to avoid real HTTP.

Run with:
    python -m unittest tests.test_mc_coercion -v
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is on sys.path.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers for loading driver_hook with an isolated temp environment
# ---------------------------------------------------------------------------


def _load_driver_hook():
    """Import driver_hook.py as a fresh module (not cached).

    Returns:
        The driver_hook module object.
    """
    hooks_dir = PROJECT_ROOT / "hooks"
    spec = importlib.util.spec_from_file_location(
        "driver_hook", hooks_dir / "driver_hook.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class McCoercionTestBase(unittest.TestCase):
    """Base class that sets up a temp directory for flag + state files.

    Each test gets:
        self.tmp_dir     — Path to a temporary directory
        self.flag_file   — Path that acts as the flag file
        self.state_dir   — Path used as MC_STATE_DIR
        self.hook        — Fresh driver_hook module with env patched
    """

    def setUp(self) -> None:
        self._tmpdir_ctx = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir_ctx.name)
        self.tmp_dir = tmp
        self.flag_file = tmp / "multiple-choice.on"
        self.state_dir = tmp / "mc_reprompt"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Patch env so driver_hook uses our temp state dir.
        self._env_patcher = patch.dict(
            os.environ, {"MC_STATE_DIR": str(self.state_dir)}
        )
        self._env_patcher.start()

        # Load a fresh module instance so env patches are picked up.
        self.hook = _load_driver_hook()

        # Patch _mc_flag_path to return our temp flag path.
        self._flag_patcher = patch.object(
            self.hook, "_mc_flag_path", return_value=self.flag_file
        )
        self._flag_patcher.start()

    def tearDown(self) -> None:
        self._flag_patcher.stop()
        self._env_patcher.stop()
        self._tmpdir_ctx.cleanup()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def enable_mc(self) -> None:
        """Touch the flag file to turn MC coercion ON."""
        self.flag_file.touch()

    def disable_mc(self) -> None:
        """Remove the flag file to turn MC coercion OFF."""
        self.flag_file.unlink(missing_ok=True)

    def reprompt_count(self, session_id: str) -> int:
        """Read the reprompt counter for a session from the temp state dir."""
        return self.hook._read_reprompt_count(session_id)

    def _capture_stop(self, stdin_data: dict) -> dict:
        """Run run_stop_hook with stdin_data and capture its stdout JSON.

        _post_ask is patched to always raise so the hook never blocks on HTTP.
        We only care about the MC-layer block decisions (which print before
        calling _post_ask) and the fallback path (when _post_ask raises).

        Returns:
            Parsed JSON dict emitted to stdout.
        """
        with patch.object(self.hook, "_post_ask", side_effect=RuntimeError("no dashboard")):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                self.hook.run_stop_hook(stdin_data)
                return json.loads(mock_out.getvalue().strip())

    def _capture_userpromptsubmit(self, stdin_data: dict) -> dict:
        """Run run_userpromptsubmit_hook and capture its stdout JSON.

        Returns:
            Parsed JSON dict emitted to stdout.
        """
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self.hook.run_userpromptsubmit_hook(stdin_data)
            return json.loads(mock_out.getvalue().strip())

    def _make_transcript(self, assistant_text: str) -> Path:
        """Write a minimal JSONL transcript with one assistant message.

        Args:
            assistant_text: The text content for the assistant turn.

        Returns:
            Path to the temp transcript file.
        """
        entry = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": assistant_text}]
            },
        }
        p = self.tmp_dir / "transcript.jsonl"
        p.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        return p


# ---------------------------------------------------------------------------
# 1. OPTION_PATTERN detector tests
# ---------------------------------------------------------------------------


class TestOptionPatternDetector(McCoercionTestBase):
    """Tests for _response_has_options and OPTION_PATTERN."""

    def test_format_1paren_matches(self) -> None:
        """'1) Title: desc' format is detected."""
        text = "Here is my response.\n1) Do the thing: because reasons\n2) Skip it: not needed"
        self.assertTrue(self.hook._response_has_options(text))

    def test_format_1dot_matches(self) -> None:
        """'1. Title: desc' format is detected."""
        text = "Answer:\n1. Option one: first choice\n2. Option two: second choice"
        self.assertTrue(self.hook._response_has_options(text))

    def test_format_paren1_matches(self) -> None:
        """'(1) Title: desc' format is detected."""
        text = "Options:\n(1) Alpha: the first\n(2) Beta: the second"
        self.assertTrue(self.hook._response_has_options(text))

    def test_single_option_line_sufficient(self) -> None:
        """A single numbered option line is enough to pass."""
        text = "Long prose.\n1) Only one: just one option"
        self.assertTrue(self.hook._response_has_options(text))

    def test_indented_option_matches(self) -> None:
        """Leading whitespace before the number is accepted."""
        text = "  1) Indented option: fine"
        self.assertTrue(self.hook._response_has_options(text))

    def test_plain_prose_no_match(self) -> None:
        """Plain prose without numbered options does not match."""
        text = "I recommend doing X because of Y. You should also consider Z."
        self.assertFalse(self.hook._response_has_options(text))

    def test_numbered_list_without_content_no_match(self) -> None:
        """A bare number with close paren but no following content does not match."""
        # '1) ' with nothing after — regex requires \S after the space
        text = "1) "
        self.assertFalse(self.hook._response_has_options(text))

    def test_markdown_bold_no_match(self) -> None:
        """Markdown bullets without numbers do not match."""
        text = "- Option A: something\n- Option B: something else"
        self.assertFalse(self.hook._response_has_options(text))

    def test_number_in_middle_of_prose_no_match(self) -> None:
        """A number mid-sentence does not trigger the detector."""
        text = "There are 3) reasons why this is good."
        # '3) ' followed by non-space — but it's not at line start / after indent
        # Our pattern uses re.MULTILINE so '^' matches line start.
        # '3) reasons' mid-sentence → does NOT start after optional whitespace
        # Actually 'There are 3)' — the 3 is not at the start of the line.
        self.assertFalse(self.hook._response_has_options(text))

    def test_empty_string_no_match(self) -> None:
        """Empty text does not match."""
        self.assertFalse(self.hook._response_has_options(""))

    def test_mc_instruction_itself_matches(self) -> None:
        """The MC_INSTRUCTION example lines pass the detector."""
        # The instruction tells Claude to use '1) Title: desc' — verify our
        # own format passes the detector.
        text = "1) Option title: brief description\n2) Another: second"
        self.assertTrue(self.hook._response_has_options(text))


# ---------------------------------------------------------------------------
# 2. MC flag helpers
# ---------------------------------------------------------------------------


class TestMcFlagHelpers(McCoercionTestBase):
    """Tests for _mc_flag_on."""

    def test_flag_on_when_file_present(self) -> None:
        """_mc_flag_on returns True when the flag file exists."""
        self.enable_mc()
        self.assertTrue(self.hook._mc_flag_on())

    def test_flag_off_when_file_absent(self) -> None:
        """_mc_flag_on returns False when the flag file does not exist."""
        self.disable_mc()
        self.assertFalse(self.hook._mc_flag_on())


# ---------------------------------------------------------------------------
# 3. build_userpromptsubmit_decision
# ---------------------------------------------------------------------------


class TestBuildUserPromptSubmitDecision(McCoercionTestBase):
    """Tests for build_userpromptsubmit_decision."""

    def test_mc_on_includes_additional_context(self) -> None:
        """When mc_on=True the decision contains additionalContext."""
        result = self.hook.build_userpromptsubmit_decision(True)
        inner = result["hookSpecificOutput"]
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        self.assertIn("additionalContext", inner)
        self.assertGreater(len(inner["additionalContext"]), 0)

    def test_mc_off_no_additional_context(self) -> None:
        """When mc_on=False the decision has no additionalContext."""
        result = self.hook.build_userpromptsubmit_decision(False)
        inner = result["hookSpecificOutput"]
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        self.assertNotIn("additionalContext", inner)

    def test_additional_context_contains_format_example(self) -> None:
        """additionalContext includes the option format so Claude knows what to emit."""
        result = self.hook.build_userpromptsubmit_decision(True)
        ctx = result["hookSpecificOutput"]["additionalContext"]
        # The context should show '1)' format so Claude produces detectable output.
        self.assertIn("1)", ctx)

    def test_hook_event_name_always_userpromptsubmit(self) -> None:
        """hookEventName is always 'UserPromptSubmit'."""
        for mc_on in (True, False):
            with self.subTest(mc_on=mc_on):
                result = self.hook.build_userpromptsubmit_decision(mc_on)
                self.assertEqual(
                    result["hookSpecificOutput"]["hookEventName"],
                    "UserPromptSubmit",
                )


# ---------------------------------------------------------------------------
# 4. Per-session reprompt state machine
# ---------------------------------------------------------------------------


class TestRepromptStateMachine(McCoercionTestBase):
    """Tests for the full reprompt-once state machine across UserPromptSubmit
    and Stop hook invocations."""

    SESSION = "test-session-abc"

    def test_userpromptsubmit_resets_counter_when_mc_on(self) -> None:
        """UserPromptSubmit resets the reprompt counter to 0 when MC is ON."""
        self.enable_mc()
        # Manually set counter to 1 (as if a reprompt already happened).
        self.hook._write_reprompt_count(self.SESSION, 1)
        self.assertEqual(self.reprompt_count(self.SESSION), 1)

        self._capture_userpromptsubmit({"session_id": self.SESSION})

        self.assertEqual(self.reprompt_count(self.SESSION), 0)

    def test_userpromptsubmit_does_not_touch_counter_when_mc_off(self) -> None:
        """UserPromptSubmit does NOT write a counter file when MC is OFF."""
        self.disable_mc()
        # No counter file should be created.
        state_file = self.hook._session_state_path(self.SESSION)
        self.assertFalse(state_file.exists())

        self._capture_userpromptsubmit({"session_id": self.SESSION})

        self.assertFalse(state_file.exists())

    def test_first_stop_blocks_when_options_missing(self) -> None:
        """First Stop call blocks with a reprompt when options are absent."""
        self.enable_mc()
        # Counter starts at 0 (no state file).
        transcript = self._make_transcript("Here is my plain prose answer with no options.")

        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        self.assertEqual(result.get("decision"), "block")
        self.assertIn("reason", result)
        self.assertGreater(len(result["reason"]), 0)

    def test_first_stop_increments_counter(self) -> None:
        """First Stop block increments the reprompt counter to 1."""
        self.enable_mc()
        transcript = self._make_transcript("No options here at all.")

        self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        self.assertEqual(self.reprompt_count(self.SESSION), 1)

    def test_second_stop_falls_through_when_options_still_missing(self) -> None:
        """Second Stop (counter==1) falls through even if options are still absent.

        'Fall through' here means the MC layer does NOT emit a block decision;
        it falls to the existing #130 dashboard path. Since we mock _post_ask
        to raise, the fallback {} (empty let-stop) is returned instead of a
        block decision — the key assertion is that decision != block with the
        MC reprompt reason.
        """
        self.enable_mc()
        self.hook._write_reprompt_count(self.SESSION, 1)
        transcript = self._make_transcript("Still no options after reprompt.")

        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        # Should NOT be the MC reprompt block — it's either {} (dashboard
        # fallback) or whatever the dashboard returned. The MC reprompt block
        # carries MC_REPROMPT_REASON in its reason field.
        reason = result.get("reason", "")
        self.assertNotIn("numbered", reason.lower(),
                         msg="Second stop should not re-emit the numbered-options reprompt")

    def test_stop_does_not_block_when_options_present(self) -> None:
        """Stop falls through (no MC block) when options are already present."""
        self.enable_mc()
        transcript = self._make_transcript(
            "Here is my answer.\n1) Do X: because A\n2) Do Y: because B"
        )

        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        # MC layer should not block — falls through to dashboard path.
        # _post_ask raises → fallback {} returned.
        reason = result.get("reason", "")
        self.assertNotIn("numbered", reason.lower(),
                         msg="Stop should not block when options are present")

    def test_stop_counter_not_incremented_when_options_present(self) -> None:
        """Counter is not incremented when options are present (no block issued)."""
        self.enable_mc()
        transcript = self._make_transcript(
            "My answer.\n1) Alpha: first\n2) Beta: second"
        )

        self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        # Counter should remain 0 (fall-through, no block).
        self.assertEqual(self.reprompt_count(self.SESSION), 0)

    def test_stop_mc_off_never_blocks_for_options(self) -> None:
        """When MC is OFF, Stop never issues an MC reprompt block."""
        self.disable_mc()
        transcript = self._make_transcript("Plain prose, no options, MC is off.")

        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        reason = result.get("reason", "")
        self.assertNotIn("numbered", reason.lower(),
                         msg="MC OFF: stop should not issue MC reprompt block")

    def test_full_turn_lifecycle(self) -> None:
        """UserPromptSubmit reset → first Stop blocks → second Stop falls through."""
        self.enable_mc()

        # 1. Simulate first user turn start: UPS resets counter.
        result_ups = self._capture_userpromptsubmit({"session_id": self.SESSION})
        inner = result_ups["hookSpecificOutput"]
        self.assertIn("additionalContext", inner)
        self.assertEqual(self.reprompt_count(self.SESSION), 0)

        # 2. Claude responds without options → first Stop blocks.
        transcript = self._make_transcript("I will do the task. No options.")
        result1 = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })
        self.assertEqual(result1.get("decision"), "block")
        self.assertEqual(self.reprompt_count(self.SESSION), 1)

        # 3. Claude responds again without options → second Stop falls through.
        result2 = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })
        reason2 = result2.get("reason", "")
        self.assertNotIn("numbered", reason2.lower())

        # 4. New user turn resets counter back to 0.
        self._capture_userpromptsubmit({"session_id": self.SESSION})
        self.assertEqual(self.reprompt_count(self.SESSION), 0)

        # 5. With fresh counter, Stop blocks again (new turn, first missing → block).
        result3 = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })
        self.assertEqual(result3.get("decision"), "block")


# ---------------------------------------------------------------------------
# 5. run_userpromptsubmit_hook output shapes
# ---------------------------------------------------------------------------


class TestRunUserPromptSubmitHook(McCoercionTestBase):
    """Integration-style tests for run_userpromptsubmit_hook output."""

    SESSION = "ups-session-xyz"

    def test_mc_on_emits_additional_context(self) -> None:
        """When flag ON, hook emits JSON with additionalContext."""
        self.enable_mc()
        result = self._capture_userpromptsubmit({"session_id": self.SESSION})
        self.assertIn("hookSpecificOutput", result)
        inner = result["hookSpecificOutput"]
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        self.assertIn("additionalContext", inner)

    def test_mc_off_emits_minimal_json(self) -> None:
        """When flag OFF, hook emits JSON without additionalContext."""
        self.disable_mc()
        result = self._capture_userpromptsubmit({"session_id": self.SESSION})
        self.assertIn("hookSpecificOutput", result)
        inner = result["hookSpecificOutput"]
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        self.assertNotIn("additionalContext", inner)

    def test_emitted_json_is_valid(self) -> None:
        """Hook always emits valid JSON regardless of flag state."""
        for mc_on in (True, False):
            with self.subTest(mc_on=mc_on):
                if mc_on:
                    self.enable_mc()
                else:
                    self.disable_mc()
                # _capture_userpromptsubmit already does json.loads; if it
                # doesn't raise, the JSON is valid.
                self._capture_userpromptsubmit({"session_id": self.SESSION})


# ---------------------------------------------------------------------------
# 6. Reprompt reason content
# ---------------------------------------------------------------------------


class TestRepromptReasonContent(McCoercionTestBase):
    """Tests that the block reason from Stop instructs Claude to add options."""

    SESSION = "reason-session"

    def test_block_reason_mentions_numbered_format(self) -> None:
        """The MC reprompt block reason references the numbered format."""
        self.enable_mc()
        transcript = self._make_transcript("No options in this response.")
        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })
        self.assertEqual(result.get("decision"), "block")
        reason = result.get("reason", "")
        # Reason should tell Claude about numbered options.
        self.assertTrue(
            "numbered" in reason.lower() or "1)" in reason,
            msg=f"Reprompt reason should mention numbered format, got: {reason!r}",
        )

    def test_block_reason_shows_option_format_example(self) -> None:
        """The MC reprompt reason includes '1)' so Claude knows the format."""
        self.enable_mc()
        transcript = self._make_transcript("Still no options.")
        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })
        self.assertIn("1)", result.get("reason", ""))


# ---------------------------------------------------------------------------
# 7. Existing #130 stop behavior preserved when MC is OFF
# ---------------------------------------------------------------------------


class TestExistingStopBehaviorPreserved(McCoercionTestBase):
    """Ensure the #130 stop hook behaviors are intact when MC is OFF.

    We verify that build_stop_decision still works correctly (the function
    is unchanged), and that run_stop_hook falls through to the dashboard
    path (or fallback) when MC is OFF.
    """

    def test_build_stop_decision_continue(self) -> None:
        """build_stop_decision('continue') still returns block+reason."""
        result = self.hook.build_stop_decision({"choice_id": "continue", "custom_text": None})
        self.assertEqual(result["decision"], "block")

    def test_build_stop_decision_stop(self) -> None:
        """build_stop_decision('stop') still returns empty dict."""
        result = self.hook.build_stop_decision({"choice_id": "stop", "custom_text": None})
        self.assertEqual(result, {})

    def test_run_stop_hook_off_no_mc_side_effects(self) -> None:
        """With MC OFF, no counter file is created by run_stop_hook."""
        self.disable_mc()
        session_id = "off-session-test"
        transcript = self._make_transcript("Plain prose, MC off.")
        state_file = self.hook._session_state_path(session_id)
        self.assertFalse(state_file.exists())

        with patch.object(self.hook, "_post_ask", side_effect=RuntimeError("no dashboard")):
            with patch("sys.stdout", new_callable=io.StringIO):
                self.hook.run_stop_hook({
                    "session_id": session_id,
                    "transcript_path": str(transcript),
                })

        self.assertFalse(state_file.exists(),
                         msg="MC OFF: stop hook should not create a counter file")


# ---------------------------------------------------------------------------
# 8. State file robustness
# ---------------------------------------------------------------------------


class TestStateFileRobustness(McCoercionTestBase):
    """Edge cases for the per-session counter file."""

    SESSION = "robustness-session"

    def test_missing_state_file_treated_as_zero(self) -> None:
        """_read_reprompt_count returns 0 when no state file exists."""
        self.assertEqual(self.reprompt_count(self.SESSION), 0)

    def test_corrupt_state_file_treated_as_zero(self) -> None:
        """_read_reprompt_count returns 0 when the state file is corrupt."""
        p = self.hook._session_state_path(self.SESSION)
        p.write_text("not-a-number", encoding="utf-8")
        self.assertEqual(self.reprompt_count(self.SESSION), 0)

    def test_session_id_sanitized_to_safe_filename(self) -> None:
        """Unusual session IDs (slashes, dots) are sanitized to safe filenames."""
        weird_id = "abc/def/../../../etc/passwd"
        p = self.hook._session_state_path(weird_id)
        # Must stay within the state_dir
        try:
            p.relative_to(self.state_dir)
        except ValueError:
            self.fail("_session_state_path produced a path outside state_dir")

    def test_empty_session_id_uses_default(self) -> None:
        """Empty session_id falls back to 'default' filename, not an error."""
        p = self.hook._session_state_path("")
        self.assertEqual(p.name, "default")


# ---------------------------------------------------------------------------
# 9. Fix 1 — full-text option detection (long responses)
# ---------------------------------------------------------------------------


class TestFullTextDetection(McCoercionTestBase):
    """Fix 1: options at the END of a long response must be detected.

    The bug: _read_last_assistant_text(transcript, max_chars=500) was called
    at the MC detection site, so options after the first 500 chars were
    invisible and the hook would spuriously block on every long compliant turn.
    Fix: call _read_last_assistant_text with max_chars=None at the detection
    site so the full text is scanned.
    """

    SESSION = "fulltext-session"

    def test_long_compliant_response_not_blocked(self) -> None:
        """Stop does NOT block when options appear after the first 500 chars.

        The assistant text is >500 chars of prose followed by a numbered list
        at the end. With the bug (max_chars=500) the options are invisible and
        the hook blocks; with the fix (max_chars=None) they are found and the
        hook falls through.
        """
        self.enable_mc()

        # Build a response whose options start well past char 500.
        preamble = "A" * 600  # 600 chars of prose — options will start at char 600+
        options_block = "\n1) Refactor: clean up the module\n2) Deploy: push to prod\n3) Wait: gather more info"
        long_text = preamble + options_block

        transcript = self._make_transcript(long_text)

        result = self._capture_stop({
            "session_id": self.SESSION,
            "transcript_path": str(transcript),
        })

        # MC layer should NOT block — options are present (past char 500).
        # _post_ask raises → fallback {} returned when not blocked.
        reason = result.get("reason", "")
        self.assertNotEqual(
            result.get("decision"), "block",
            msg="Stop should NOT block: options are present past char 500 — "
                "full-text scan required. reason=%r" % reason,
        )


# ---------------------------------------------------------------------------
# 10. Fix 2 — hardened OPTION_PATTERN for bolded/bulleted output
# ---------------------------------------------------------------------------


class TestHardenedOptionPattern(McCoercionTestBase):
    """Fix 2: OPTION_PATTERN must accept bolded/bulleted formats Claude emits.

    New accepted forms:
        **1)** Title       — bolded marker only
        **1. Title: y**    — whole line bolded
        - 1) Title         — leading bullet before number

    Still rejected:
        - Option A          — bullet with no number
        1)                  — bare marker, nothing after
        There are 3) reasons — mid-sentence number
        plain prose
    """

    # ---- Positive (must match) ------------------------------------------------

    def test_bolded_marker_paren_matches(self) -> None:
        """'**1)** Title' is detected as an option."""
        text = "Here is my response.\n**1)** Refactor: clean the code\n**2)** Deploy: push it"
        self.assertTrue(
            self.hook._response_has_options(text),
            "**1)** format should match",
        )

    def test_bolded_whole_line_dot_matches(self) -> None:
        """'**1. Title: desc**' (whole line bolded) is detected."""
        text = "Let me suggest:\n**1. Refactor: clean it up**\n**2. Deploy: push now**"
        self.assertTrue(
            self.hook._response_has_options(text),
            "**1. Title: desc** format should match",
        )

    def test_bullet_then_number_matches(self) -> None:
        """'- 1) Title' (leading bullet before number) is detected."""
        text = "Options:\n- 1) Alpha: first choice\n- 2) Beta: second choice"
        self.assertTrue(
            self.hook._response_has_options(text),
            "- 1) format should match",
        )

    def test_star_bullet_then_number_matches(self) -> None:
        """'* 1) Title' (asterisk bullet before number) is detected."""
        text = "Options:\n* 1) Alpha: first\n* 2) Beta: second"
        self.assertTrue(
            self.hook._response_has_options(text),
            "* 1) format should match",
        )

    # ---- Negative (must NOT match) -------------------------------------------

    def test_bullet_no_number_no_match(self) -> None:
        """'- Option A' (bullet without number) does not match."""
        text = "- Option A: something\n- Option B: something else"
        self.assertFalse(
            self.hook._response_has_options(text),
            "plain bullet without number should not match",
        )

    def test_bare_marker_nothing_after_no_match(self) -> None:
        """'1) ' with nothing after (trailing space only) does not match."""
        text = "1) "
        self.assertFalse(
            self.hook._response_has_options(text),
            "bare marker with no following content should not match",
        )

    def test_mid_sentence_number_no_match(self) -> None:
        """A number mid-sentence ('There are 3) reasons') does not match."""
        text = "There are 3) reasons why this is good."
        self.assertFalse(
            self.hook._response_has_options(text),
            "mid-sentence number should not match",
        )

    def test_plain_prose_no_match(self) -> None:
        """Plain prose without any numbered marker does not match."""
        text = "I recommend doing X because of Y. You should also consider Z."
        self.assertFalse(
            self.hook._response_has_options(text),
            "plain prose should not match",
        )


# ---------------------------------------------------------------------------
# 11. Fix 3 — opportunistic prune of stale session counter files
# ---------------------------------------------------------------------------


import time  # noqa: E402  (import inside module is fine for tests)


class TestStaleCounterPrune(McCoercionTestBase):
    """Fix 3: counter files older than 7 days are pruned when the state dir
    is accessed.  Fresh files are kept; the prune never raises."""

    def test_old_counter_pruned_new_counter_kept(self) -> None:
        """File >7 days old is deleted; file created just now is kept."""
        # Create an "old" counter file and back-date its mtime by 8 days.
        old_file = self.state_dir / "old-session"
        old_file.write_text("1", encoding="utf-8")
        eight_days_ago = time.time() - (8 * 24 * 3600)
        os.utime(str(old_file), (eight_days_ago, eight_days_ago))

        # Create a fresh counter file (mtime = now).
        fresh_file = self.state_dir / "fresh-session"
        fresh_file.write_text("0", encoding="utf-8")

        # Trigger the prune by calling _mc_state_dir() (the access point
        # that the hook calls every time it reads/writes state).
        self.hook._mc_state_dir()

        self.assertFalse(old_file.exists(), "8-day-old counter file should be pruned")
        self.assertTrue(fresh_file.exists(), "fresh counter file should be kept")

    def test_prune_does_not_raise_on_permission_error(self) -> None:
        """A prune failure (e.g. permission error) must never crash the hook."""
        # We simulate a prune that fails by patching os.remove/Path.unlink.
        # The hook must not raise.
        with patch("os.unlink", side_effect=OSError("permission denied")):
            try:
                self.hook._mc_state_dir()
            except Exception as exc:  # pragma: no cover
                self.fail(f"_mc_state_dir raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
