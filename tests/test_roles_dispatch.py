"""Tests for roles dispatcher (SP2 Task 11).

Roles is a read-only collection in SP2:
    - GET /roles              -> list available role definitions
    - GET /roles?target=permissions  -> check_tool_permission

No POST/PATCH/PUT/DELETE — role *assignment* to sessions happens through
sessions (target='roles'), not here.
"""
import asyncio
import json
import unittest
from unittest.mock import MagicMock


from iterm_mcpy.tools.roles import RolesDispatcher, roles


def _make_ctx(role_manager=None, logger=None, **extra):
    """Build a fake MCP Context with a lifespan_context dict.

    role_manager defaults to a MagicMock. Extra keys go into lifespan_context.
    """
    ctx = MagicMock()
    rm = role_manager if role_manager is not None else MagicMock()
    ctx.request_context.lifespan_context = {
        "role_manager": rm,
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _make_assignment(role_value="devops"):
    """Build a stand-in for SessionRoleAssignment."""
    assignment = MagicMock()
    role = MagicMock()
    role.value = role_value
    assignment.role = role
    return assignment


# ========================================================================= #
# OPTIONS                                                                   #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="OPTIONS"))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "roles")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("HEAD", parsed["data"]["methods"])
        self.assertIn("OPTIONS", parsed["data"]["methods"])
        # Read-only: no state-mutating methods advertised.
        self.assertNotIn("POST", parsed["data"]["methods"])
        self.assertNotIn("PATCH", parsed["data"]["methods"])
        self.assertNotIn("DELETE", parsed["data"]["methods"])
        # Sub-resource 'permissions' should be advertised.
        self.assertIn("permissions", parsed["data"]["sub_resources"])

    def test_schema_verb_works(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="schema"))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# GET /roles — list available role definitions                              #
# ========================================================================= #


class TestListAvailable(unittest.TestCase):
    def test_list_returns_role_catalog(self):
        # Uses real DEFAULT_ROLE_CONFIGS / SessionRole — no mocking needed.
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="list"))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])

        # Count should cover every SessionRole enum member.
        from core.models import SessionRole
        expected_count = len(list(SessionRole))
        self.assertEqual(parsed["data"]["count"], expected_count)

        role_list = parsed["data"]["roles"]
        role_names = [r["role"] for r in role_list]
        self.assertIn("devops", role_names)
        self.assertIn("builder", role_names)
        self.assertIn("orchestrator", role_names)
        self.assertIn("custom", role_names)

    def test_list_includes_config_fields(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="list"))
        self.assertTrue(parsed["ok"])

        devops = next(r for r in parsed["data"]["roles"] if r["role"] == "devops")
        self.assertIn("description", devops)
        self.assertIn("available_tools", devops)
        self.assertIn("restricted_tools", devops)
        self.assertIn("default_commands", devops)
        self.assertIn("can_spawn_agents", devops)
        self.assertIn("can_modify_roles", devops)
        self.assertIn("priority", devops)
        # Orchestrator should have can_modify_roles=True.
        orch = next(r for r in parsed["data"]["roles"] if r["role"] == "orchestrator")
        self.assertTrue(orch["can_modify_roles"])
        self.assertTrue(orch["can_spawn_agents"])

    def test_list_via_get(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="GET"))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertGreater(parsed["data"]["count"], 0)

    def test_list_no_role_manager_ok(self):
        # _list_available doesn't touch role_manager — uses DEFAULT_ROLE_CONFIGS.
        # Verify it works even with an empty lifespan context.
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {}
        parsed = asyncio.run(roles(ctx=ctx, op="list"))
        self.assertTrue(parsed["ok"])
        self.assertGreater(parsed["data"]["count"], 0)


# ========================================================================= #
# GET /roles?target=permissions — check_tool_permission                     #
# ========================================================================= #


class TestCheckPermission(unittest.TestCase):
    def test_check_allowed_returns_true(self):
        rm = MagicMock()
        rm.is_tool_allowed.return_value = (True, None)
        rm.get_role.return_value = _make_assignment(role_value="builder")

        parsed = asyncio.run(roles(
            ctx=_make_ctx(role_manager=rm),
            op="GET", target="permissions",
            session_id="s-1", tool_name="npm",
        ))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["session_id"], "s-1")
        self.assertEqual(parsed["data"]["tool_name"], "npm")
        self.assertTrue(parsed["data"]["allowed"])
        self.assertIsNone(parsed["data"]["reason"])
        self.assertEqual(parsed["data"]["role"], "builder")
        self.assertTrue(parsed["data"]["has_role"])
        rm.is_tool_allowed.assert_called_once_with("s-1", "npm")

    def test_check_denied_returns_false_with_reason(self):
        rm = MagicMock()
        rm.is_tool_allowed.return_value = (
            False,
            "Tool 'rm' is restricted for role researcher",
        )
        rm.get_role.return_value = _make_assignment(role_value="researcher")

        parsed = asyncio.run(roles(
            ctx=_make_ctx(role_manager=rm),
            op="GET", target="permissions",
            session_id="s-2", tool_name="rm",
        ))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["allowed"])
        self.assertIn("restricted", parsed["data"]["reason"])
        self.assertEqual(parsed["data"]["role"], "researcher")
        self.assertTrue(parsed["data"]["has_role"])

    def test_check_unassigned_session_has_role_false(self):
        rm = MagicMock()
        # No role assigned — is_tool_allowed returns allowed=True.
        rm.is_tool_allowed.return_value = (True, None)
        rm.get_role.return_value = None

        parsed = asyncio.run(roles(
            ctx=_make_ctx(role_manager=rm),
            op="GET", target="permissions",
            session_id="s-3", tool_name="ls",
        ))
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["allowed"])
        self.assertFalse(parsed["data"]["has_role"])
        self.assertIsNone(parsed["data"]["role"])

    def test_check_via_check_verb(self):
        rm = MagicMock()
        rm.is_tool_allowed.return_value = (True, None)
        rm.get_role.return_value = None

        parsed = asyncio.run(roles(
            ctx=_make_ctx(role_manager=rm),
            op="check", target="permissions",
            session_id="s-1", tool_name="git",
        ))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["allowed"])

    def test_check_missing_session_id_returns_err(self):
        parsed = asyncio.run(roles(
            ctx=_make_ctx(),
            op="GET", target="permissions",
            tool_name="npm",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("session_id", parsed["error"]["message"].lower())

    def test_check_missing_tool_name_returns_err(self):
        parsed = asyncio.run(roles(
            ctx=_make_ctx(),
            op="GET", target="permissions",
            session_id="s-1",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("tool_name", parsed["error"]["message"].lower())

    def test_check_missing_role_manager_returns_err(self):
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {}
        parsed = asyncio.run(roles(
            ctx=ctx,
            op="GET", target="permissions",
            session_id="s-1", tool_name="npm",
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("role_manager", parsed["error"]["message"])


# ========================================================================= #
# HEAD                                                                      #
# ========================================================================= #


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="HEAD"))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# Unknown op / unsupported methods                                          #
# ========================================================================= #


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = asyncio.run(roles(ctx=_make_ctx(), op="frobnicate"))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestUnsupportedMethods(unittest.TestCase):
    """Roles is read-only — POST/PATCH/PUT/DELETE must not be implemented."""

    def test_post_not_implemented(self):
        parsed = asyncio.run(
            roles(ctx=_make_ctx(), op="POST", definer="CREATE")
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())

    def test_patch_not_implemented(self):
        parsed = asyncio.run(
            roles(ctx=_make_ctx(), op="PATCH", definer="MODIFY")
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())

    def test_delete_not_implemented(self):
        parsed = asyncio.run(
            roles(ctx=_make_ctx(), op="DELETE")
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())


# ========================================================================= #
# Dispatcher direct instantiation (sanity check)                            #
# ========================================================================= #


class TestDispatcherDirect(unittest.TestCase):
    def test_collection_name(self):
        d = RolesDispatcher()
        self.assertEqual(d.collection, "roles")
        self.assertIn("permissions", d.sub_resources)


if __name__ == "__main__":
    unittest.main()
