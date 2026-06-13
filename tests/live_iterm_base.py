"""Base class for live-iTerm2 integration tests.

Every test that creates iTerm2 windows/sessions should inherit from
:class:`LiveItermTestCase`.  The base class integrates with the existing
``async_setup`` / ``async_teardown`` / ``run_async_test`` pattern used in
the live test modules, without breaking the event-loop structure.

Design
------
The existing live modules share a common pattern::

    def run_async_test(self, coro):
        async def test_wrapper():
            try:
                await self.async_setup()
                await coro()
            finally:
                await self.async_teardown()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(test_wrapper())
        loop.close()

:class:`LiveItermTestCase` subclasses ``unittest.IsolatedAsyncioTestCase``
but the existing ``test_*`` methods remain **synchronous** and call
``run_async_test`` as before.  The base class overrides ``run_async_test``
to inject:

1. A unique per-run tag (``self._tag``).
2. A call to :func:`~core.test_window_tracker.close_tagged_sessions` in
   the ``finally`` block of the wrapper, using the **same event loop** as
   the test so the iTerm2 connection object is valid.

Subclasses override ``async_setup`` (instead of ``asyncSetUp``) to create
their window via :meth:`create_tagged_window`, and ``async_teardown`` for
any extra per-test cleanup they need (the base class will close tagged
sessions automatically *after* the subclass teardown).

Usage::

    from tests.live_iterm_base import LiveItermTestCase

    class TestFoo(LiveItermTestCase):
        async def async_setup(self):
            await super().async_setup()
            self.test_session = await self.create_tagged_window()
            await self.test_session.set_name("FooSession")

        async def async_teardown(self):
            if hasattr(self, "test_session") and self.test_session.is_monitoring:
                await self.test_session.stop_monitoring()
            # Base class closes tagged sessions automatically — no need to
            # call terminal.close_session() manually.
            await super().async_teardown()

        def test_something(self):
            async def test_impl():
                output = await self.test_session.get_screen_contents()
                self.assertIn("$", output)
            self.run_async_test(test_impl)
"""

import asyncio
import logging
import shutil
import tempfile
import unittest
from typing import Callable, Coroutine, Optional

import iterm2

from core.terminal import ItermTerminal
from core.session import ItermSession
from core.test_window_tracker import (
    TAG_PREFIX,
    close_tagged_sessions,
    make_run_tag,
    mark_session,
)

log = logging.getLogger("iterm-mcp.live-test-base")


class LiveItermTestCase(unittest.IsolatedAsyncioTestCase):
    """IsolatedAsyncioTestCase with per-run window tagging and safe teardown.

    Subclasses MUST call ``await super().async_setup()`` from their own
    ``async_setup`` to establish the connection and tag.  The base
    ``run_async_test`` automatically calls ``close_tagged_sessions`` in its
    ``finally`` block (same event loop as the test).

    Attributes:
        connection: The active ``iterm2.Connection`` (set by async_setup).
        terminal: An initialised :class:`~core.terminal.ItermTerminal`.
        _tag: The per-run tag string (e.g. ``MCP-TEST·12345-a1b2c3d4``).
        _log_dir: Temporary directory for session logs.
    """

    # ------------------------------------------------------------------
    # async_setup / async_teardown — called by run_async_test
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Establish an iTerm2 connection and set up the tagged terminal.

        Subclasses must call ``await super().async_setup()`` first.
        """
        self._log_dir = tempfile.mkdtemp()
        self._tag = make_run_tag()

        try:
            self.connection = await iterm2.Connection.async_create()
            self.terminal = ItermTerminal(
                connection=self.connection,
                log_dir=self._log_dir,
                enable_logging=True,
            )
            await self.terminal.initialize()
        except Exception as exc:
            self.fail(f"LiveItermTestCase.async_setup: failed to connect to iTerm2: {exc}")

    async def async_teardown(self) -> None:
        """Hook for per-test cleanup.

        Subclasses may override; they SHOULD call ``await super().async_teardown()``
        or at minimum not suppress exceptions raised here.  The base
        implementation is a no-op; session cleanup is handled by
        ``run_async_test``.
        """

    # ------------------------------------------------------------------
    # create_tagged_window — open a window owned by this run
    # ------------------------------------------------------------------

    async def create_tagged_window(
        self,
        profile: Optional[str] = None,
        name: Optional[str] = None,
    ) -> ItermSession:
        """Create a new iTerm2 window and tag it for teardown.

        Calls :meth:`~core.terminal.ItermTerminal.create_window` and
        immediately sets ``user.mcp_test_run = self._tag`` on the raw
        iTerm2 session so that :func:`~core.test_window_tracker.close_tagged_sessions`
        will close it.

        Args:
            profile: Optional iTerm2 profile name.
            name: Optional name to set on the session after creation.

        Returns:
            The :class:`~core.session.ItermSession` for the new window.

        Raises:
            AssertionError: If the window could not be created.
        """
        try:
            session = await self.terminal.create_window(profile=profile)
        except Exception as exc:
            self.fail(f"create_tagged_window: failed to create window: {exc}")

        # Tag the raw iterm2.Session (not the ItermSession wrapper).
        raw = session.session
        await mark_session(raw, self._tag)

        if name:
            await session.set_name(name)
            await asyncio.sleep(0.1)

        return session

    # ------------------------------------------------------------------
    # run_async_test — replacement for the manual event-loop pattern
    # ------------------------------------------------------------------

    def run_async_test(self, coro: Callable[[], Coroutine]) -> None:
        """Run an async test function with guaranteed tagged teardown.

        Replaces the manual ``asyncio.new_event_loop()`` pattern in the
        original test modules.  The flow is::

            await self.async_setup()
            await coro()
            [finally]
            await self.async_teardown()
            await close_tagged_sessions(self.connection, self._tag)
            shutil.rmtree(self._log_dir)

        All of these run in the **same** event loop so the iTerm2
        connection object is valid throughout.

        Args:
            coro: A zero-argument async callable (the test implementation).
        """
        async def _wrapper() -> None:
            await self.async_setup()
            try:
                await coro()
            finally:
                # Per-test cleanup first (subclass teardown).
                try:
                    await self.async_teardown()
                except Exception as exc:
                    log.warning("async_teardown raised: %s", exc)

                # Close all tagged sessions for this run.
                if hasattr(self, "connection") and hasattr(self, "_tag"):
                    try:
                        closed = await close_tagged_sessions(
                            self.connection, self._tag
                        )
                        log.info(
                            "run_async_test teardown: closed %d session(s) for tag=%s",
                            closed,
                            self._tag,
                        )
                    except Exception as exc:
                        log.warning("close_tagged_sessions raised: %s", exc)

                # Clean up temp log dir.
                if hasattr(self, "_log_dir"):
                    shutil.rmtree(self._log_dir, ignore_errors=True)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_wrapper())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
