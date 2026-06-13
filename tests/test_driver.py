"""
Tests for the ControIDE Phase-0 browser driver.

Coverage:
- DriverStore unit tests (synchronous and asyncio)
- Hook decision-shaping logic (pure Python, no HTTP, no iTerm2)
- Full ask/answer flow contract test (asyncio)

Run with:
    python -m unittest tests/test_driver.py -v
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is on sys.path so `core` and `hooks` are importable.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# DriverStore synchronous tests
# ---------------------------------------------------------------------------


class TestDriverStoreSynchronous(unittest.TestCase):
    """Tests for DriverStore that do not need a running event loop."""

    def setUp(self) -> None:
        # Import inside setUp so test isolation is clean.
        from core.driver import DriverStore

        self.store = DriverStore()
        self.default_options = [
            {"id": "continue", "label": "Continue", "text": "Continue on."},
            {"id": "stop", "label": "Stop here", "text": ""},
        ]

    def test_post_question_creates_question(self) -> None:
        """post_question returns a Question with a non-empty id."""
        q = self.store.post_question(
            hook_type="stop",
            prompt="What next?",
            options=self.default_options,
        )
        self.assertIsNotNone(q.id)
        self.assertGreater(len(q.id), 0)
        self.assertEqual(q.hook_type, "stop")
        self.assertEqual(q.prompt, "What next?")
        self.assertIsNone(q.answer)

    def test_get_question_returns_same_object(self) -> None:
        """get_question returns the same Question that was posted."""
        q = self.store.post_question("stop", "Prompt", self.default_options)
        retrieved = self.store.get_question(q.id)
        self.assertIs(retrieved, q)

    def test_get_question_unknown_id_returns_none(self) -> None:
        """get_question returns None for an unknown id."""
        result = self.store.get_question("does-not-exist")
        self.assertIsNone(result)

    def test_answer_question_sets_answer(self) -> None:
        """answer_question sets the answer dict on the Question."""
        q = self.store.post_question("stop", "Prompt", self.default_options)
        success = self.store.answer_question(q.id, "continue", None)
        self.assertTrue(success)
        self.assertIsNotNone(q.answer)
        self.assertEqual(q.answer["choice_id"], "continue")
        self.assertIsNone(q.answer["custom_text"])

    def test_answer_question_with_custom_text(self) -> None:
        """answer_question stores custom_text when provided."""
        q = self.store.post_question("stop", "Prompt", self.default_options)
        self.store.answer_question(q.id, "custom", "Do X instead")
        self.assertEqual(q.answer["choice_id"], "custom")
        self.assertEqual(q.answer["custom_text"], "Do X instead")

    def test_answer_unknown_id_returns_false(self) -> None:
        """answer_question returns False for an unknown id."""
        result = self.store.answer_question("bogus-id", "continue")
        self.assertFalse(result)

    def test_pending_questions_lists_unanswered(self) -> None:
        """pending_questions only returns unanswered questions."""
        q1 = self.store.post_question("stop", "Q1", self.default_options)
        q2 = self.store.post_question("stop", "Q2", self.default_options)
        self.store.answer_question(q1.id, "stop")

        pending = self.store.pending_questions()
        pending_ids = [q.id for q in pending]
        self.assertNotIn(q1.id, pending_ids)
        self.assertIn(q2.id, pending_ids)


# ---------------------------------------------------------------------------
# DriverStore asyncio tests
# ---------------------------------------------------------------------------


class TestDriverStoreAsyncio(unittest.IsolatedAsyncioTestCase):
    """Asyncio tests for DriverStore.wait_for_answer."""

    async def asyncSetUp(self) -> None:
        from core.driver import DriverStore

        self.store = DriverStore()
        self.options = [
            {"id": "allow", "label": "Allow", "text": "allow"},
            {"id": "deny", "label": "Deny", "text": "deny"},
        ]

    async def test_wait_for_answer_resolves(self) -> None:
        """wait_for_answer unblocks when the question is answered."""
        q = self.store.post_question("pretooluse", "Tool call?", self.options)

        async def answer_shortly():
            await asyncio.sleep(0.05)
            self.store.answer_question(q.id, "allow")

        asyncio.create_task(answer_shortly())
        result = await self.store.wait_for_answer(q.id, timeout=5.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["choice_id"], "allow")

    async def test_wait_for_answer_timeout(self) -> None:
        """wait_for_answer returns None when timeout elapses with no answer."""
        q = self.store.post_question("pretooluse", "Tool call?", self.options)
        result = await self.store.wait_for_answer(q.id, timeout=0.05)
        self.assertIsNone(result)

    async def test_wait_for_answer_unknown_id(self) -> None:
        """wait_for_answer returns None for an unknown question id."""
        result = await self.store.wait_for_answer("no-such-id", timeout=0.1)
        self.assertIsNone(result)

    async def test_wait_for_answer_evicts_on_answered(self) -> None:
        """_questions is empty after wait_for_answer returns on the answered path."""
        q = self.store.post_question("pretooluse", "Allow?", self.options)

        async def answer_shortly():
            await asyncio.sleep(0.02)
            self.store.answer_question(q.id, "allow")

        asyncio.create_task(answer_shortly())
        result = await self.store.wait_for_answer(q.id, timeout=5.0)

        self.assertIsNotNone(result)
        self.assertNotIn(q.id, self.store._questions)

    async def test_wait_for_answer_evicts_on_timeout(self) -> None:
        """_questions is empty after wait_for_answer returns on the timeout path."""
        q = self.store.post_question("pretooluse", "Allow?", self.options)
        result = await self.store.wait_for_answer(q.id, timeout=0.02)

        self.assertIsNone(result)
        self.assertNotIn(q.id, self.store._questions)


# ---------------------------------------------------------------------------
# Hook decision-shaping helpers
# ---------------------------------------------------------------------------
# We test the shaping logic directly without spawning a subprocess or
# requiring an HTTP server. The helper functions are imported from
# hooks/driver_hook.py by adding the hooks directory to sys.path.


def _import_hook_helpers():
    """Import decision-shaping functions from driver_hook.py."""
    hooks_dir = PROJECT_ROOT / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "driver_hook", hooks_dir / "driver_hook.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStopHookDecisions(unittest.TestCase):
    """Test the stop-hook decision-shaping logic in driver_hook.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = _import_hook_helpers()

    def _shape(self, choice_id: str, custom_text=None) -> dict:
        answer = {"choice_id": choice_id, "custom_text": custom_text}
        return self.hook.build_stop_decision(answer)

    def test_stop_hook_continue_decision(self) -> None:
        """choice_id='continue' → block with the option's text."""
        result = self._shape("continue")
        self.assertEqual(result["decision"], "block")
        self.assertIn("Continue", result["reason"])

    def test_stop_hook_refine_decision(self) -> None:
        """choice_id='refine' → block with a clarification reason."""
        result = self._shape("refine")
        self.assertEqual(result["decision"], "block")
        self.assertGreater(len(result["reason"]), 0)

    def test_stop_hook_stop_decision(self) -> None:
        """choice_id='stop' → empty dict (let Claude stop naturally)."""
        result = self._shape("stop")
        self.assertEqual(result, {})

    def test_stop_hook_custom_decision(self) -> None:
        """choice_id='custom' with custom_text → block with that text."""
        result = self._shape("custom", custom_text="Please check the tests first.")
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["reason"], "Please check the tests first.")

    def test_stop_hook_custom_no_text_falls_back(self) -> None:
        """choice_id='custom' with no text → empty dict."""
        result = self._shape("custom", custom_text=None)
        self.assertEqual(result, {})


class TestPreToolUseHookDecisions(unittest.TestCase):
    """Test the pretooluse hook decision-shaping logic in driver_hook.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = _import_hook_helpers()

    def _shape(self, choice_id: str, custom_text=None) -> dict:
        answer = {"choice_id": choice_id, "custom_text": custom_text}
        return self.hook.build_pretooluse_decision(answer)

    def _extract(self, result: dict) -> dict:
        return result["hookSpecificOutput"]

    def test_pretooluse_allow_decision(self) -> None:
        """choice_id='allow' → permissionDecision='allow'."""
        result = self._shape("allow")
        inner = self._extract(result)
        self.assertEqual(inner["hookEventName"], "PreToolUse")
        self.assertEqual(inner["permissionDecision"], "allow")

    def test_pretooluse_deny_decision(self) -> None:
        """choice_id='deny' → permissionDecision='deny'."""
        result = self._shape("deny")
        inner = self._extract(result)
        self.assertEqual(inner["permissionDecision"], "deny")

    def test_pretooluse_ask_decision(self) -> None:
        """choice_id='ask' → permissionDecision='ask'."""
        result = self._shape("ask")
        inner = self._extract(result)
        self.assertEqual(inner["permissionDecision"], "ask")

    def test_pretooluse_hook_event_name_always_set(self) -> None:
        """hookEventName is always 'PreToolUse'."""
        for choice_id in ("allow", "deny", "ask"):
            with self.subTest(choice_id=choice_id):
                result = self._shape(choice_id)
                inner = self._extract(result)
                self.assertEqual(inner["hookEventName"], "PreToolUse")


# ---------------------------------------------------------------------------
# Full ask/answer flow contract test (asyncio)
# ---------------------------------------------------------------------------


class TestAskAnswerFullFlow(unittest.IsolatedAsyncioTestCase):
    """End-to-end contract test for the DriverStore ask/answer cycle."""

    async def test_ask_answer_full_flow(self) -> None:
        """Post a question, answer it concurrently, verify the answer."""
        from core.driver import DriverStore

        store = DriverStore()
        options = [
            {"id": "continue", "label": "Continue", "text": "Continue."},
            {"id": "stop", "label": "Stop", "text": ""},
        ]

        q = store.post_question("stop", "Confirm action?", options)
        question_id = q.id

        # Start waiting for the answer in a separate task.
        wait_task = asyncio.create_task(
            store.wait_for_answer(question_id, timeout=5.0)
        )

        # Give the wait task a moment to start waiting, then answer.
        await asyncio.sleep(0.02)
        answered = store.answer_question(question_id, "continue")

        result = await wait_task

        self.assertTrue(answered)
        self.assertIsNotNone(result)
        self.assertEqual(result["choice_id"], "continue")
        self.assertIsNone(result["custom_text"])

    async def test_ask_answer_flow_with_custom_text(self) -> None:
        """Custom text is propagated through the full flow."""
        from core.driver import DriverStore

        store = DriverStore()
        options = [
            {"id": "custom", "label": "Custom…", "text": ""},
        ]

        q = store.post_question("stop", "Do something?", options)
        wait_task = asyncio.create_task(
            store.wait_for_answer(q.id, timeout=5.0)
        )

        await asyncio.sleep(0.02)
        store.answer_question(q.id, "custom", "Run the linter first.")

        result = await wait_task
        self.assertEqual(result["choice_id"], "custom")
        self.assertEqual(result["custom_text"], "Run the linter first.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
