"""Tests for the `projects` MCP tool (grouping)."""
import asyncio, unittest
from unittest.mock import AsyncMock, MagicMock, patch
from iterm_mcpy.tools.projects import projects as projects_tool


def _ctx(terminal):
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"terminal": terminal, "logger": MagicMock(),
                                            "agent_registry": MagicMock()}
    return ctx


class TestProjectsTool(unittest.TestCase):
    def _s(self, sid):
        s = MagicMock(); s.id = sid; s.name = sid; return s

    def test_groups_sessions_by_project(self):
        terminal = MagicMock(); terminal.sessions = {"a": self._s("a"), "b": self._s("b"), "c": self._s("c")}
        async def fake_proj(conn, sid):
            return {"a": "/repoA", "b": "/repoA", "c": "/repoB"}[sid]
        with patch("iterm_mcpy.tools.projects.get_session_project", new=fake_proj):
            parsed = asyncio.run(projects_tool(ctx=_ctx(terminal), op="GET"))
        groups = {g["project"]: sorted(g["sessions"]) for g in parsed["data"]}
        self.assertEqual(groups, {"/repoA": ["a", "b"], "/repoB": ["c"]})

    def test_options_returns_schema(self):
        parsed = asyncio.run(projects_tool(ctx=_ctx(MagicMock(sessions={})), op="OPTIONS"))
        self.assertEqual(parsed["method"], "OPTIONS")

    def test_unassigned_bucket_for_none_project(self):
        terminal = MagicMock(); terminal.sessions = {"a": self._s("a"), "b": self._s("b")}
        async def fake_proj(conn, sid):
            return "/repoA" if sid == "a" else None
        with patch("iterm_mcpy.tools.projects.get_session_project", new=fake_proj):
            parsed = asyncio.run(projects_tool(ctx=_ctx(terminal), op="GET"))
        groups = {g["project"]: sorted(g["sessions"]) for g in parsed["data"]}
        self.assertEqual(groups["(unassigned)"], ["b"])
        self.assertEqual(groups["/repoA"], ["a"])


if __name__ == "__main__":
    unittest.main()
