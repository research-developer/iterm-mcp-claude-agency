"""Tests for services dispatcher (SP2 Task 10)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from iterm_mcpy.tools.services import ServicesDispatcher, services


def _make_ctx(service_manager=None, logger=None, **extra):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys go straight into `lifespan_context` so tests can inject
    whichever managers they need. The service_manager defaults to a
    MagicMock with AsyncMock methods for the async calls.
    """
    ctx = MagicMock()

    sm = service_manager
    if sm is None:
        sm = MagicMock()
        sm.check_service_running = AsyncMock(return_value=False)
        sm.get_inactive_services = AsyncMock(return_value=[])
        sm.start_service = AsyncMock()
        sm.stop_service = AsyncMock(return_value=True)

    ctx.request_context.lifespan_context = {
        "service_manager": sm,
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _fake_service(
    name="svc",
    display_name=None,
    priority_value="optional",
    command="run",
    port=None,
    working_directory=None,
):
    """Build a stand-in for ServiceConfig with the attributes read by the dispatcher."""
    s = MagicMock()
    s.name = name
    s.effective_display_name = display_name or name
    priority = MagicMock()
    priority.value = priority_value
    s.priority = priority
    s.command = command
    s.port = port
    s.working_directory = working_directory
    return s


def _fake_service_state(is_running=True, session_id="sess-1", error_message=None):
    """Build a stand-in for ServiceState with the attributes read by the dispatcher."""
    state = MagicMock()
    state.is_running = is_running
    state.session_id = session_id
    state.error_message = error_message
    return state


def _fake_registry(services=None):
    """Build a stand-in for ServiceRegistry with a mutable services list."""
    r = MagicMock()
    r.services = list(services or [])
    return r


# ========================================================================= #
# OPTIONS / HEAD / unknown verb                                             #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = json.loads(asyncio.run(services(ctx=_make_ctx(), op="OPTIONS")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "services")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("PATCH", parsed["data"]["methods"])
        self.assertIn("DELETE", parsed["data"]["methods"])
        # Sub-resource 'inactive' should be advertised for inactive services.
        self.assertIn("inactive", parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(services(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])
        self.assertIn("TRIGGER", post["definers"])

    def test_options_lists_patch_definers(self):
        parsed = json.loads(asyncio.run(services(ctx=_make_ctx(), op="OPTIONS")))
        patch_meta = parsed["data"]["methods"]["PATCH"]
        self.assertIn("MODIFY", patch_meta["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(services(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(services(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE belongs to the PUT family, not POST.
        parsed = json.loads(asyncio.run(
            services(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# GET /services — list                                                      #
# ========================================================================= #


class TestList(unittest.TestCase):
    def test_list_global_services(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[
            _fake_service(name="api", display_name="API", priority_value="required",
                          command="uvicorn api:app", port=8000),
            _fake_service(name="worker", priority_value="optional", command="celery"),
        ])
        sm.check_service_running = AsyncMock(return_value=False)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        service_list = parsed["data"]["services"]
        self.assertEqual(service_list[0]["name"], "api")
        self.assertEqual(service_list[0]["display_name"], "API")
        self.assertEqual(service_list[0]["priority"], "required")
        self.assertEqual(service_list[0]["command"], "uvicorn api:app")
        self.assertEqual(service_list[0]["port"], 8000)
        self.assertFalse(service_list[0]["is_running"])
        self.assertEqual(service_list[1]["name"], "worker")
        # repo_path is None (not passed)
        self.assertIsNone(parsed["data"].get("repo_path"))

    def test_list_with_repo_path_uses_merged(self):
        sm = MagicMock()
        sm.get_merged_services.return_value = [
            _fake_service(name="db", priority_value="preferred", command="postgres"),
        ]
        sm.check_service_running = AsyncMock(return_value=True)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 1)
        self.assertEqual(parsed["data"]["services"][0]["name"], "db")
        self.assertTrue(parsed["data"]["services"][0]["is_running"])
        self.assertEqual(parsed["data"]["repo_path"], "/repo")
        sm.get_merged_services.assert_called_once()

    def test_list_skip_status_when_include_status_false(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[
            _fake_service(name="api"),
        ])
        sm.check_service_running = AsyncMock(return_value=False)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list",
            include_status=False,
        )))
        self.assertTrue(parsed["ok"])
        # is_running field should NOT be present when include_status=False.
        self.assertNotIn("is_running", parsed["data"]["services"][0])
        sm.check_service_running.assert_not_awaited()

    def test_list_with_min_priority_filters_global(self):
        from core.services import ServicePriority

        sm = MagicMock()
        # Use real ServicePriority objects so the ordering compare works.
        svc_required = MagicMock()
        svc_required.name = "api"
        svc_required.effective_display_name = "api"
        svc_required.priority = ServicePriority.REQUIRED
        svc_required.command = "uvicorn"
        svc_required.port = None
        svc_required.working_directory = None
        svc_quiet = MagicMock()
        svc_quiet.name = "log"
        svc_quiet.effective_display_name = "log"
        svc_quiet.priority = ServicePriority.QUIET
        svc_quiet.command = "tail"
        svc_quiet.port = None
        svc_quiet.working_directory = None

        sm.load_global_config.return_value = _fake_registry(services=[
            svc_required, svc_quiet,
        ])
        sm.check_service_running = AsyncMock(return_value=False)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list",
            min_priority="preferred",
        )))
        self.assertTrue(parsed["ok"])
        # Only 'api' (required) should pass the preferred+ filter.
        self.assertEqual(parsed["data"]["count"], 1)
        self.assertEqual(parsed["data"]["services"][0]["name"], "api")

    def test_list_empty(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)
        self.assertEqual(parsed["data"]["services"], [])


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="HEAD",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# GET /services?target=inactive — list_inactive                             #
# ========================================================================= #


class TestListInactive(unittest.TestCase):
    def test_list_inactive_returns_services(self):
        sm = MagicMock()
        sm.get_inactive_services = AsyncMock(return_value=[
            _fake_service(name="db", priority_value="required", command="postgres"),
            _fake_service(name="cache", priority_value="preferred", command="redis"),
        ])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="GET", target="inactive",
            repo_path="/repo",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        items = parsed["data"]["inactive_services"]
        self.assertEqual(items[0]["name"], "db")
        self.assertEqual(items[0]["priority"], "required")
        self.assertEqual(items[1]["name"], "cache")
        self.assertEqual(parsed["data"]["repo_path"], "/repo")

    def test_list_inactive_via_legacy_op(self):
        sm = MagicMock()
        sm.get_inactive_services = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list_inactive",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["data"]["count"], 0)

    def test_list_inactive_missing_repo_path_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="GET", target="inactive",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("repo_path", parsed["error"]["message"].lower())

    def test_list_inactive_with_min_priority(self):
        from core.services import ServicePriority

        sm = MagicMock()
        sm.get_inactive_services = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list_inactive",
            repo_path="/repo",
            min_priority="preferred",
        )))
        self.assertTrue(parsed["ok"])
        # min_priority should have been converted to ServicePriority and passed through.
        args, kwargs = sm.get_inactive_services.call_args
        self.assertEqual(args[0], "/repo")
        self.assertEqual(args[1], ServicePriority.PREFERRED)


# ========================================================================= #
# POST /services (CREATE) — add                                             #
# ========================================================================= #


class TestAdd(unittest.TestCase):
    def test_add_to_global_registry(self):
        sm = MagicMock()
        registry = _fake_registry(services=[])
        sm.load_global_config.return_value = registry

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="add",
            service_name="api",
            command="uvicorn api:app",
            priority="required",
            port=8000,
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["service"], "api")
        self.assertEqual(parsed["data"]["scope"], "global")
        self.assertTrue(parsed["data"]["added"])
        # Must have persisted through save_global_config.
        sm.save_global_config.assert_called_once_with(registry)
        # The registry must now contain our new service.
        self.assertEqual(len(registry.services), 1)
        self.assertEqual(registry.services[0].name, "api")

    def test_add_via_post_plus_definer(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="POST", definer="CREATE",
            service_name="api",
            command="uvicorn api:app",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "CREATE")
        # priority defaults to 'optional' when unspecified.
        sm.save_global_config.assert_called_once()

    def test_add_to_repo_registry(self):
        sm = MagicMock()
        registry = _fake_registry(services=[])
        sm.load_repo_config.return_value = registry

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="add",
            service_name="db",
            command="postgres",
            scope="repo",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["scope"], "repo")
        sm.save_repo_config.assert_called_once_with("/repo", registry)
        sm.save_global_config.assert_not_called()

    def test_add_replaces_existing_service_with_same_name(self):
        from core.services import ServiceConfig, ServicePriority

        sm = MagicMock()
        existing = ServiceConfig(
            name="api", command="old-cmd", priority=ServicePriority.OPTIONAL,
        )
        registry = _fake_registry(services=[existing])
        sm.load_global_config.return_value = registry

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="add",
            service_name="api",
            command="new-cmd",
        )))
        self.assertTrue(parsed["ok"])
        # Replacement should leave exactly one entry with the new command.
        self.assertEqual(len(registry.services), 1)
        self.assertEqual(registry.services[0].name, "api")
        self.assertEqual(registry.services[0].command, "new-cmd")

    def test_add_missing_service_name_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="add",
            command="run",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("service_name", parsed["error"]["message"].lower())

    def test_add_missing_command_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="add",
            service_name="api",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("command", parsed["error"]["message"].lower())

    def test_add_repo_scope_without_repo_path_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="add",
            service_name="api",
            command="run",
            scope="repo",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("repo_path", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /services/{name}/runs (TRIGGER) — start                              #
# ========================================================================= #


class TestStart(unittest.TestCase):
    def test_start_service_via_friendly_verb(self):
        svc = _fake_service(name="api", command="uvicorn")
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[svc])
        sm.start_service = AsyncMock(return_value=_fake_service_state(
            is_running=True, session_id="s-1",
        ))

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="start",
            service_name="api",
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["service"], "api")
        self.assertTrue(parsed["data"]["started"])
        self.assertEqual(parsed["data"]["session_id"], "s-1")
        sm.start_service.assert_awaited_once()

    def test_start_via_post_plus_trigger(self):
        svc = _fake_service(name="api")
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[svc])
        sm.start_service = AsyncMock(return_value=_fake_service_state())

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="POST", definer="TRIGGER",
            service_name="api",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "TRIGGER")

    def test_start_with_repo_path_uses_merged(self):
        svc = _fake_service(name="db")
        sm = MagicMock()
        sm.get_merged_services.return_value = [svc]
        sm.start_service = AsyncMock(return_value=_fake_service_state())

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="start",
            service_name="db",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        sm.get_merged_services.assert_called_once_with("/repo")
        sm.load_global_config.assert_not_called()

    def test_start_service_not_found_returns_err(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[
            _fake_service(name="other"),
        ])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="start",
            service_name="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])
        # The error should include the available services.
        self.assertIn("other", parsed["error"]["message"])

    def test_start_failed_returns_failure_state(self):
        svc = _fake_service(name="api")
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[svc])
        sm.start_service = AsyncMock(return_value=_fake_service_state(
            is_running=False, session_id=None, error_message="port in use",
        ))

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="start",
            service_name="api",
        )))
        # Envelope is ok=true (handler completed); the data payload reports
        # started=false and the error_message. This mirrors legacy behavior.
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["started"])
        self.assertEqual(parsed["data"]["error"], "port in use")

    def test_start_missing_service_name_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="start",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("service_name", parsed["error"]["message"].lower())


# ========================================================================= #
# PATCH /services/{name} (MODIFY) — configure                               #
# ========================================================================= #


class TestConfigure(unittest.TestCase):
    def test_configure_via_friendly_verb(self):
        from core.services import ServiceConfig, ServicePriority

        existing = ServiceConfig(
            name="api", command="old", priority=ServicePriority.OPTIONAL, port=8000,
        )
        sm = MagicMock()
        registry = _fake_registry(services=[existing])
        sm.load_global_config.return_value = registry

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="configure",
            service_name="api",
            priority="required",
            port=8080,
            command="new-cmd",
        )))
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["service"], "api")
        self.assertEqual(parsed["data"]["scope"], "global")
        self.assertTrue(parsed["data"]["updated"])
        sm.save_global_config.assert_called_once_with(registry)

        # The in-registry entry should be the updated ServiceConfig.
        updated = registry.services[0]
        self.assertEqual(updated.name, "api")
        self.assertEqual(updated.command, "new-cmd")
        self.assertEqual(updated.priority, ServicePriority.REQUIRED)
        self.assertEqual(updated.port, 8080)

    def test_configure_via_patch_plus_modify(self):
        from core.services import ServiceConfig, ServicePriority

        existing = ServiceConfig(name="api", command="old")
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[existing])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="PATCH", definer="MODIFY",
            service_name="api",
            priority="preferred",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "MODIFY")

    def test_configure_repo_scope(self):
        from core.services import ServiceConfig

        existing = ServiceConfig(name="db", command="postgres")
        sm = MagicMock()
        registry = _fake_registry(services=[existing])
        sm.load_repo_config.return_value = registry

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="configure",
            service_name="db",
            command="postgres --new-flag",
            scope="repo",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["scope"], "repo")
        sm.save_repo_config.assert_called_once_with("/repo", registry)
        sm.save_global_config.assert_not_called()

    def test_configure_not_found_returns_err(self):
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="configure",
            service_name="missing",
            priority="required",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("missing", parsed["error"]["message"])
        # Must not have saved anything.
        sm.save_global_config.assert_not_called()

    def test_configure_missing_service_name_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="configure",
            priority="required",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("service_name", parsed["error"]["message"].lower())

    def test_configure_repo_scope_without_repo_path_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="configure",
            service_name="api",
            scope="repo",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("repo_path", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE /services/{name}/runs — stop                                       #
# ========================================================================= #


class TestStop(unittest.TestCase):
    def test_stop_service_via_friendly_verb(self):
        sm = MagicMock()
        sm.stop_service = AsyncMock(return_value=True)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="stop",
            service_name="api",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["service"], "api")
        self.assertTrue(parsed["data"]["stopped"])
        sm.stop_service.assert_awaited_once_with("api")

    def test_stop_via_delete(self):
        sm = MagicMock()
        sm.stop_service = AsyncMock(return_value=True)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="DELETE",
            service_name="api",
        )))
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["stopped"])

    def test_stop_not_running_returns_stopped_false(self):
        sm = MagicMock()
        sm.stop_service = AsyncMock(return_value=False)

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="stop",
            service_name="api",
        )))
        # Envelope is ok=true (handler completed); data reports stopped=false.
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["stopped"])

    def test_stop_missing_service_name_returns_err(self):
        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(),
            op="stop",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("service_name", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported combinations                                                  #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_send_not_implemented(self):
        parsed = json.loads(asyncio.run(
            services(ctx=_make_ctx(), op="POST", definer="SEND")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_post_invoke_not_implemented(self):
        parsed = json.loads(asyncio.run(
            services(ctx=_make_ctx(), op="POST", definer="INVOKE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_patch_rename_not_implemented(self):
        parsed = json.loads(asyncio.run(
            services(ctx=_make_ctx(), op="PATCH", definer="RENAME")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_put_not_implemented(self):
        parsed = json.loads(asyncio.run(
            services(ctx=_make_ctx(), op="PUT", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())


# ========================================================================= #
# Legacy manage_services op strings (backwards compatibility)               #
# ========================================================================= #


class TestLegacyOpInterop(unittest.TestCase):
    """Verify services-specific legacy op strings are mapped locally."""

    def test_legacy_configure_op(self):
        from core.services import ServiceConfig, ServicePriority

        existing = ServiceConfig(name="api", command="old")
        sm = MagicMock()
        sm.load_global_config.return_value = _fake_registry(services=[existing])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="configure",
            service_name="api",
            priority="required",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")

    def test_legacy_list_inactive_op(self):
        sm = MagicMock()
        sm.get_inactive_services = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(services(
            ctx=_make_ctx(service_manager=sm),
            op="list_inactive",
            repo_path="/repo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")


if __name__ == "__main__":
    unittest.main()
