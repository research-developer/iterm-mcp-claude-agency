"""Tests for the process-level AppContext singleton."""
import asyncio
import unittest
from unittest import mock


class TestAppContextMapping(unittest.TestCase):
    def test_getitem_returns_field(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext(logger="fake-logger")
        self.assertEqual(ctx["logger"], "fake-logger")

    def test_getitem_unknown_key_raises_keyerror(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext()
        with self.assertRaises(KeyError):
            ctx["nope"]

    def test_get_returns_default_for_unknown_key(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext()
        self.assertIsNone(ctx.get("nope"))
        self.assertEqual(ctx.get("nope", 7), 7)

    def test_contains(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext(terminal="t")
        self.assertIn("terminal", ctx)
        self.assertNotIn("nope", ctx)


class TestSingleton(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import iterm_mcpy.app_context as ac
        ac._app_context = None  # reset between tests

    async def asyncTearDown(self):
        import iterm_mcpy.app_context as ac
        ac._app_context = None

    async def test_concurrent_calls_build_once_and_share_instance(self):
        import iterm_mcpy.app_context as ac
        from iterm_mcpy.app_context import AppContext, get_app_context
        calls = 0

        async def fake_build():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)  # widen the race window
            return AppContext(logger="built")

        with mock.patch.object(ac, "_build_app_context", fake_build):
            results = await asyncio.gather(*[get_app_context() for _ in range(5)])
        self.assertEqual(calls, 1)
        self.assertTrue(all(r is results[0] for r in results))

    async def test_shutdown_clears_singleton(self):
        import iterm_mcpy.app_context as ac
        from iterm_mcpy.app_context import AppContext, get_app_context, shutdown_app_context

        async def fake_build():
            return AppContext(logger="built")

        with mock.patch.object(ac, "_build_app_context", fake_build):
            first = await get_app_context()
            await shutdown_app_context()
            second = await get_app_context()
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
