"""
ControIDE Phase-0 driver: ask/answer router for human-in-the-loop hooks.

This module is intentionally free of iterm2, fastmcp, and any other
dependencies that require an active iTerm2 connection. It manages a
registry of pending questions that hook scripts post and wait on, while
the browser-side driver page answers them via the dashboard server.

Typical lifecycle
-----------------
1. A hook script calls POST /api/ask → server calls DriverStore.post_question()
2. Server broadcasts a "question" SSE event to the driver.html page
3. Human clicks a tile → browser calls POST /api/answer
4. Server calls DriverStore.answer_question() which sets the asyncio.Event
5. DriverStore.wait_for_answer() unblocks and returns the answer dict
6. Hook script receives the answer and emits structured JSON to stdout
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol


# ---------------------------------------------------------------------------
# Input abstraction (mirrors ControIDE base.py; seam for future controllers)
# ---------------------------------------------------------------------------


class Action(Enum):
    """Abstract input actions.

    These map to tile navigation and selection, independent of the physical
    input device (mouse, keyboard, gamepad, voice).

    TODO: implement GamepadController (buttons → MOVE_PREV/MOVE_NEXT/SELECT)
    TODO: implement DictationController (speech tokens → Action)
    """

    MOVE_PREV = "move_prev"
    MOVE_NEXT = "move_next"
    SELECT = "select"
    CANCEL = "cancel"


class Controller(Protocol):
    """Input source abstraction.

    Any object implementing this protocol can drive the tile selection UI.
    The browser driver uses DOM click events; future controllers may use
    a gamepad or dictation engine.

    Args:
        event: Device-specific event dict (key press, button state, etc.)

    Returns:
        The mapped Action, or None if the event is not actionable.
    """

    def handle_event(self, event: dict) -> Optional[Action]:
        """Map a device event to an Action, or return None."""
        ...


# ---------------------------------------------------------------------------
# Question dataclass
# ---------------------------------------------------------------------------


@dataclass
class Question:
    """A pending question waiting for a human answer.

    Attributes:
        id: UUID string used as the correlation key.
        hook_type: "stop" or "pretooluse".
        prompt: Human-readable summary shown in the driver page.
        options: List of {"id": str, "label": str, "text": str} dicts.
        answer_event: Set when an answer is available.
        answer: Populated by answer_question(); None until then.
    """

    id: str
    hook_type: str
    prompt: str
    options: list
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    answer: Optional[dict] = None


# ---------------------------------------------------------------------------
# DriverStore
# ---------------------------------------------------------------------------


class DriverStore:
    """Thread-safe in-process store for pending questions.

    All mutating methods are synchronous and safe to call from coroutines
    running on the same event loop (no concurrent writes from threads).
    wait_for_answer is async and must be awaited inside a running loop.
    """

    def __init__(self) -> None:
        self._questions: dict[str, Question] = {}

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def post_question(
        self,
        hook_type: str,
        prompt: str,
        options: list,
    ) -> Question:
        """Create and store a new pending question.

        Args:
            hook_type: "stop" or "pretooluse".
            prompt: Human-readable text shown on the driver page.
            options: List of option dicts with keys "id", "label", "text".

        Returns:
            The newly created Question (answer_event not yet set).
        """
        question_id = str(uuid.uuid4())
        question = Question(
            id=question_id,
            hook_type=hook_type,
            prompt=prompt,
            options=options,
        )
        self._questions[question_id] = question
        return question

    def get_question(self, question_id: str) -> Optional[Question]:
        """Return the Question with the given id, or None if not found.

        Args:
            question_id: UUID string.

        Returns:
            The Question, or None.
        """
        return self._questions.get(question_id)

    def answer_question(
        self,
        question_id: str,
        choice_id: str,
        custom_text: Optional[str] = None,
    ) -> bool:
        """Record a human answer and wake any waiting coroutine.

        Args:
            question_id: UUID string of the question.
            choice_id: The option id chosen by the human.
            custom_text: Free-text input if the custom option was chosen.

        Returns:
            True if found and answered; False if question_id is unknown.
        """
        question = self._questions.get(question_id)
        if question is None:
            return False
        question.answer = {"choice_id": choice_id, "custom_text": custom_text}
        question.answer_event.set()
        return True

    def pending_questions(self) -> list:
        """Return all questions that have not yet been answered.

        Returns:
            List of Question objects whose answer is None.
        """
        return [q for q in self._questions.values() if q.answer is None]

    # ------------------------------------------------------------------
    # Async wait
    # ------------------------------------------------------------------

    async def wait_for_answer(
        self,
        question_id: str,
        timeout: float = 120.0,
    ) -> Optional[dict]:
        """Wait until the question is answered or the timeout expires.

        The question is evicted from _questions in a finally block, so both
        the answered path and the timeout/cancellation path clean up the entry.

        Args:
            question_id: UUID string of the question.
            timeout: Maximum seconds to wait (default 120).

        Returns:
            The answer dict {"choice_id": str, "custom_text": str|None},
            or None if the question was not found or the timeout elapsed.
        """
        question = self._questions.get(question_id)
        if question is None:
            return None
        answer: Optional[dict] = None
        try:
            await asyncio.wait_for(
                question.answer_event.wait(),
                timeout=timeout,
            )
            answer = question.answer
        except asyncio.TimeoutError:
            pass
        finally:
            self._questions.pop(question_id, None)
        return answer
