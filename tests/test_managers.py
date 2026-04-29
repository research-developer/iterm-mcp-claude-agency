"""Tests for managers dispatcher (SP2 Task 7)."""
import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from iterm_mcpy.tools.managers import ManagersDispatcher, managers


def _make_ctx(
    manager_registry=None,
    terminal=None,
    agent_registry=None,
    logger=None,
    **extra,
):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys go straight into `lifespan_context` so tests can inject
    whichever managers they need.
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "manager_registry": manager_registry or MagicMock(),
        "terminal": terminal or MagicMock(),
        "agent_registry": agent_registry or MagicMock(),
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _fake_manager(
    name="m1",
    workers=None,
    strategy_value="role_based",
    worker_roles=None,
    metadata=None,
    created_at_iso="2024-01-01T00:00:00+00:00",
):
    """Build a stand-in for ManagerAgent with the attributes the dispatcher reads."""
    from datetime import datetime, timezone

    m = MagicMock()
    m.name = name
    m.workers = workers if workers is not None else []
    # manager.strategy is a DelegationStrategy enum; we only need .value.
    strategy = MagicMock()
    strategy.value = strategy_value
    m.strategy = strategy
    # worker_roles is {str: SessionRole enum}; we only need {k: v.value}.
    roles = {}
    for k, v in (worker_roles or {}).items():
        role = MagicMock()
        role.value = v
        roles[k] = role
    m.worker_roles = roles
    m.metadata = metadata or {}
    m.created_at = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    # Set up a sensible default for add_worker / remove_worker return values.
    m.add_worker = MagicMock(return_value=None)
    m.remove_worker = MagicMock(return_value=True)
    return m


# ========================================================================= #
# OPTIONS / HEAD / unknown verb                                             #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = json.loads(asyncio.run(managers(ctx=_make_ctx(), op="OPTIONS")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "managers")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("DELETE", parsed["data"]["methods"])
        # Sub-resource 'workers' should be advertised for worker membership.
        self.assertIn("workers", parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(managers(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(managers(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(managers(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE is in the PUT family, not POST.
        parsed = json.loads(asyncio.run(
            managers(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# GET /managers — list                                                      #
# ========================================================================= #


class TestList(unittest.TestCase):
    def test_list_returns_managers_with_worker_counts(self):
        registry = MagicMock()
        registry.list_managers.return_value = [
            _fake_manager(
                name="m1",
                workers=["w1", "w2"],
                strategy_value="role_based",
            ),
            _fake_manager(
                name="m2",
                workers=[],
                strategy_value="round_robin",
            ),
        ]

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="list",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        manager_list = parsed["data"]["managers"]
        self.assertEqual(manager_list[0]["name"], "m1")
        self.assertEqual(manager_list[0]["workers"], ["w1", "w2"])
        self.assertEqual(manager_list[0]["worker_count"], 2)
        self.assertEqual(manager_list[0]["delegation_strategy"], "role_based")
        self.assertEqual(manager_list[1]["name"], "m2")
        self.assertEqual(manager_list[1]["worker_count"], 0)
        self.assertEqual(manager_list[1]["delegation_strategy"], "round_robin")

    def test_list_empty(self):
        registry = MagicMock()
        registry.list_managers.return_value = []
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="list",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)
        self.assertEqual(parsed["data"]["managers"], [])

    def test_list_via_get_verb(self):
        registry = MagicMock()
        registry.list_managers.return_value = []
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="GET",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        # HEAD uses GET's handler internally; our GET returns a dict, which
        # passes through project_head unchanged. That's acceptable — the
        # HEAD envelope still gets ok=true with the dict data.
        registry = MagicMock()
        registry.list_managers.return_value = []
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="HEAD",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# GET /managers/{name} — get_info                                           #
# ========================================================================= #


class TestGetInfo(unittest.TestCase):
    def test_get_info_returns_detailed_manager(self):
        registry = MagicMock()
        registry.get_manager.return_value = _fake_manager(
            name="m1",
            workers=["w1"],
            strategy_value="role_based",
            worker_roles={"w1": "builder"},
            metadata={"team": "backend"},
        )

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="GET",
            manager_name="m1",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        data = parsed["data"]
        self.assertEqual(data["name"], "m1")
        self.assertEqual(data["workers"], ["w1"])
        self.assertEqual(data["delegation_strategy"], "role_based")
        self.assertEqual(data["worker_roles"], {"w1": "builder"})
        self.assertEqual(data["metadata"], {"team": "backend"})
        self.assertIn("created_at", data)
        registry.get_manager.assert_called_once_with("m1")

    def test_get_info_not_found_returns_err(self):
        registry = MagicMock()
        registry.get_manager.return_value = None
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="GET",
            manager_name="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_get_via_get_verb_with_manager_name(self):
        # "get" is a GET verb alias; with manager_name it fetches info,
        # without manager_name it lists.
        registry = MagicMock()
        registry.get_manager.return_value = _fake_manager(name="m1")
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="get",
            manager_name="m1",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["data"]["name"], "m1")


# ========================================================================= #
# POST /managers (CREATE) — create manager                                  #
# ========================================================================= #


class TestCreateManager(unittest.TestCase):
    def test_create_manager_via_friendly_verb(self):
        registry = MagicMock()
        created = _fake_manager(
            name="m1",
            workers=["w1", "w2"],
            strategy_value="role_based",
        )
        registry.create_manager.return_value = created

        # Patch _setup_manager_callbacks to isolate from real callback wiring.
        with patch("iterm_mcpy.tools._callbacks._setup_manager_callbacks") as mock_setup:
            parsed = json.loads(asyncio.run(managers(
                ctx=_make_ctx(manager_registry=registry),
                op="create",
                manager_name="m1",
                workers=["w1", "w2"],
            )))

        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["name"], "m1")
        self.assertEqual(parsed["data"]["workers"], ["w1", "w2"])
        self.assertEqual(parsed["data"]["delegation_strategy"], "role_based")
        self.assertTrue(parsed["data"]["created"])
        registry.create_manager.assert_called_once()
        # Callback wiring must run for new managers.
        mock_setup.assert_called_once()

    def test_create_manager_via_post_plus_definer(self):
        registry = MagicMock()
        registry.create_manager.return_value = _fake_manager(
            name="m1", workers=[], strategy_value="role_based"
        )
        with patch("iterm_mcpy.tools._callbacks._setup_manager_callbacks"):
            parsed = json.loads(asyncio.run(managers(
                ctx=_make_ctx(manager_registry=registry),
                op="POST",
                definer="CREATE",
                manager_name="m1",
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "CREATE")

    def test_create_manager_missing_name_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="create",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("manager_name", parsed["error"]["message"].lower())

    def test_create_manager_with_delegation_strategy_and_roles(self):
        registry = MagicMock()
        registry.create_manager.return_value = _fake_manager(
            name="m1",
            workers=["builder-1", "tester-1"],
            strategy_value="round_robin",
        )
        with patch("iterm_mcpy.tools._callbacks._setup_manager_callbacks"):
            parsed = json.loads(asyncio.run(managers(
                ctx=_make_ctx(manager_registry=registry),
                op="create",
                manager_name="m1",
                workers=["builder-1", "tester-1"],
                delegation_strategy="round_robin",
                worker_roles={"builder-1": "builder", "tester-1": "tester"},
                metadata={"team": "backend"},
            )))
        self.assertTrue(parsed["ok"])
        # Verify create_manager was called with enum-converted values.
        kwargs = registry.create_manager.call_args.kwargs
        self.assertEqual(kwargs["name"], "m1")
        self.assertEqual(kwargs["workers"], ["builder-1", "tester-1"])
        self.assertEqual(kwargs["metadata"], {"team": "backend"})
        # delegation_strategy is a DelegationStrategy enum.
        from core.manager import DelegationStrategy, SessionRole
        self.assertEqual(kwargs["delegation_strategy"], DelegationStrategy.ROUND_ROBIN)
        # worker_roles values are SessionRole enums.
        self.assertEqual(
            kwargs["worker_roles"],
            {"builder-1": SessionRole.BUILDER, "tester-1": SessionRole.TESTER},
        )


# ========================================================================= #
# POST /managers/{name}/workers (CREATE) — add worker                       #
# ========================================================================= #


class TestAddWorker(unittest.TestCase):
    def test_add_worker_delegates_to_manager(self):
        registry = MagicMock()
        manager_mock = _fake_manager(name="m1")
        registry.get_manager.return_value = manager_mock

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="POST", definer="CREATE", target="workers",
            manager_name="m1", worker_name="w1",
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["added"])
        self.assertEqual(parsed["data"]["manager_name"], "m1")
        self.assertEqual(parsed["data"]["worker_name"], "w1")
        manager_mock.add_worker.assert_called_once_with("w1", None)

    def test_add_worker_with_role(self):
        registry = MagicMock()
        manager_mock = _fake_manager(name="m1")
        registry.get_manager.return_value = manager_mock

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="create", target="workers",
            manager_name="m1", worker_name="w1",
            worker_role="builder",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["role"], "builder")
        from core.manager import SessionRole
        manager_mock.add_worker.assert_called_once_with("w1", SessionRole.BUILDER)

    def test_add_worker_manager_not_found_returns_err(self):
        registry = MagicMock()
        registry.get_manager.return_value = None
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="create", target="workers",
            manager_name="missing", worker_name="w1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_add_worker_missing_manager_name_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="create", target="workers", worker_name="w1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("manager_name", parsed["error"]["message"].lower())

    def test_add_worker_missing_worker_name_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="create", target="workers", manager_name="m1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("worker_name", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE /managers/{name} — remove manager                                  #
# ========================================================================= #


class TestRemoveManager(unittest.TestCase):
    def test_remove_manager_delegates_to_registry(self):
        registry = MagicMock()
        registry.remove_manager.return_value = True

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="delete",
            manager_name="m1",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["removed"])
        self.assertEqual(parsed["data"]["manager_name"], "m1")
        registry.remove_manager.assert_called_once_with("m1")

    def test_remove_manager_not_found_returns_err(self):
        registry = MagicMock()
        registry.remove_manager.return_value = False
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="delete",
            manager_name="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_remove_manager_missing_name_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="delete",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("manager_name", parsed["error"]["message"].lower())

    def test_remove_manager_via_delete_method(self):
        registry = MagicMock()
        registry.remove_manager.return_value = True
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="DELETE",
            manager_name="m1",
        )))
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# DELETE /managers/{name}/workers — remove worker                           #
# ========================================================================= #


class TestRemoveWorker(unittest.TestCase):
    def test_remove_worker_delegates_to_manager(self):
        registry = MagicMock()
        manager_mock = _fake_manager(name="m1")
        manager_mock.remove_worker.return_value = True
        registry.get_manager.return_value = manager_mock

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="delete", target="workers",
            manager_name="m1", worker_name="w1",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["removed"])
        self.assertEqual(parsed["data"]["manager_name"], "m1")
        self.assertEqual(parsed["data"]["worker_name"], "w1")
        manager_mock.remove_worker.assert_called_once_with("w1")

    def test_remove_worker_not_in_manager_returns_err(self):
        registry = MagicMock()
        manager_mock = _fake_manager(name="m1")
        manager_mock.remove_worker.return_value = False
        registry.get_manager.return_value = manager_mock

        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="delete", target="workers",
            manager_name="m1", worker_name="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_remove_worker_manager_not_found_returns_err(self):
        registry = MagicMock()
        registry.get_manager.return_value = None
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(manager_registry=registry),
            op="delete", target="workers",
            manager_name="missing", worker_name="w1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])

    def test_remove_worker_missing_manager_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="DELETE", target="workers", worker_name="w1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("manager_name", parsed["error"]["message"].lower())

    def test_remove_worker_missing_worker_returns_err(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="DELETE", target="workers", manager_name="m1",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("worker_name", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported combinations                                                  #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_send_not_implemented(self):
        parsed = json.loads(asyncio.run(
            managers(ctx=_make_ctx(), op="POST", definer="SEND")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_delete_unknown_target_not_implemented(self):
        parsed = json.loads(asyncio.run(managers(
            ctx=_make_ctx(),
            op="DELETE", target="bogus",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
