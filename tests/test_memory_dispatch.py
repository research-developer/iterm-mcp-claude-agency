"""Tests for memory dispatcher (SP2 Task 9)."""
import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from iterm_mcpy.tools.memory import MemoryDispatcher, memory


def _make_ctx(memory_store=None, logger=None, **extra):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys are merged into `lifespan_context` so tests can inject
    whichever collaborators they need. The memory_store defaults to an
    AsyncMock because its methods are awaited.
    """
    ctx = MagicMock()

    store = memory_store
    if store is None:
        store = MagicMock()
        store.store = AsyncMock(return_value=None)
        store.retrieve = AsyncMock(return_value=None)
        store.search = AsyncMock(return_value=[])
        store.list_keys = AsyncMock(return_value=[])
        store.list_namespaces = AsyncMock(return_value=[])
        store.delete = AsyncMock(return_value=False)
        store.clear_namespace = AsyncMock(return_value=0)
        store.get_stats = AsyncMock(return_value={})

    ctx.request_context.lifespan_context = {
        "memory_store": store,
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _fake_memory(
    key="mykey",
    value="myvalue",
    namespace=("proj", "agent"),
    timestamp=None,
    metadata=None,
):
    """Build a stand-in for a Memory with the fields the dispatcher reads."""
    m = MagicMock()
    m.key = key
    m.value = value
    m.namespace = namespace
    m.timestamp = timestamp or datetime(2024, 1, 1, tzinfo=timezone.utc)
    m.metadata = metadata if metadata is not None else {}
    return m


def _fake_search_result(
    key="mykey",
    value="myvalue",
    namespace=("proj", "agent"),
    score=0.9,
    match_context="Key: mykey",
    timestamp=None,
    metadata=None,
):
    """Build a stand-in for a MemorySearchResult."""
    r = MagicMock()
    r.memory = _fake_memory(
        key=key,
        value=value,
        namespace=namespace,
        timestamp=timestamp,
        metadata=metadata,
    )
    r.score = score
    r.match_context = match_context
    return r


# ========================================================================= #
# OPTIONS / HEAD / unknown verb                                             #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = json.loads(asyncio.run(memory(ctx=_make_ctx(), op="OPTIONS")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "memory")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("DELETE", parsed["data"]["methods"])
        for sub in ("namespaces", "keys", "stats"):
            self.assertIn(sub, parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(memory(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(memory(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(memory(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE belongs to the PUT family, not POST.
        parsed = json.loads(asyncio.run(
            memory(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# POST /memory (CREATE) — store                                             #
# ========================================================================= #


class TestStore(unittest.TestCase):
    def test_store_via_friendly_verb(self):
        store = MagicMock()
        store.store = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="store",
            namespace=["proj", "agent"],
            key="mykey",
            value={"foo": "bar"},
            metadata={"tag": "test"},
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["status"], "stored")
        self.assertEqual(parsed["data"]["namespace"], ["proj", "agent"])
        self.assertEqual(parsed["data"]["key"], "mykey")
        store.store.assert_awaited_once_with(
            ("proj", "agent"),
            "mykey",
            {"foo": "bar"},
            {"tag": "test"},
        )

    def test_store_via_post_plus_definer(self):
        store = MagicMock()
        store.store = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="POST", definer="CREATE",
            namespace=["ns"], key="k", value="v",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertEqual(parsed["data"]["metadata"], {})

    def test_store_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="store",
            key="k", value="v",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())

    def test_store_missing_key_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="store",
            namespace=["ns"], value="v",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("key", parsed["error"]["message"].lower())

    def test_store_missing_value_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="store",
            namespace=["ns"], key="k",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("value", parsed["error"]["message"].lower())

    def test_store_invalid_namespace_char_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="store",
            namespace=["bad ns"], key="k", value="v",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("invalid", parsed["error"]["message"].lower())

    def test_store_invalid_key_char_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="store",
            namespace=["ns"], key="bad key!", value="v",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("invalid", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /memory — retrieve                                                    #
# ========================================================================= #


class TestRetrieve(unittest.TestCase):
    def test_retrieve_found(self):
        store = MagicMock()
        store.retrieve = AsyncMock(return_value=_fake_memory(
            key="mykey",
            value={"nested": [1, 2]},
            namespace=("proj", "agent"),
            metadata={"tag": "test"},
        ))

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="retrieve",
            namespace=["proj", "agent"], key="mykey",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["found"])
        self.assertEqual(parsed["data"]["key"], "mykey")
        self.assertEqual(parsed["data"]["value"], {"nested": [1, 2]})
        self.assertEqual(parsed["data"]["metadata"], {"tag": "test"})
        self.assertEqual(parsed["data"]["namespace"], ["proj", "agent"])
        store.retrieve.assert_awaited_once_with(("proj", "agent"), "mykey")

    def test_retrieve_not_found(self):
        store = MagicMock()
        store.retrieve = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET",
            namespace=["proj"], key="missing",
        )))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["found"])
        self.assertEqual(parsed["data"]["namespace"], ["proj"])
        self.assertEqual(parsed["data"]["key"], "missing")

    def test_retrieve_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="retrieve",
            key="k",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())

    def test_retrieve_missing_key_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="retrieve",
            namespace=["ns"],
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("key", parsed["error"]["message"].lower())


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        # HEAD reuses the default GET path (retrieve), which needs
        # namespace + key. We pass both so the HEAD handler succeeds.
        store = MagicMock()
        store.retrieve = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="HEAD",
            namespace=["ns"], key="k",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# GET /memory?target=search — search                                        #
# ========================================================================= #


class TestSearch(unittest.TestCase):
    def test_search_returns_results(self):
        store = MagicMock()
        store.search = AsyncMock(return_value=[
            _fake_search_result(
                key="k1", value="hello world", score=0.95,
                match_context="...hello world...",
                namespace=("proj", "agent"),
            ),
            _fake_search_result(
                key="k2", value="another hello", score=0.8,
                match_context="...another hello...",
                namespace=("proj", "agent"),
            ),
        ])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET", target="search",
            namespace=["proj", "agent"], query="hello", limit=5,
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["query"], "hello")
        self.assertEqual(parsed["data"]["count"], 2)
        self.assertEqual(parsed["data"]["results"][0]["key"], "k1")
        self.assertEqual(parsed["data"]["results"][0]["score"], 0.95)
        store.search.assert_awaited_once_with(("proj", "agent"), "hello", 5)

    def test_search_via_search_verb(self):
        # 'search' is in VERB_ATLAS as GET (no target), so we must pass
        # target="search" explicitly to reach the search handler.
        store = MagicMock()
        store.search = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="search", target="search",
            namespace=["proj"], query="foo",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)
        # limit defaults to 10 in the tool wrapper
        store.search.assert_awaited_once_with(("proj",), "foo", 10)

    def test_search_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="GET", target="search",
            query="foo",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())

    def test_search_missing_query_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="GET", target="search",
            namespace=["ns"],
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("query", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /memory?target=keys — list_keys                                       #
# ========================================================================= #


class TestListKeys(unittest.TestCase):
    def test_list_keys_returns_list(self):
        store = MagicMock()
        store.list_keys = AsyncMock(return_value=["a", "b", "c"])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET", target="keys",
            namespace=["proj"],
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 3)
        self.assertEqual(parsed["data"]["keys"], ["a", "b", "c"])
        self.assertEqual(parsed["data"]["namespace"], ["proj"])
        store.list_keys.assert_awaited_once_with(("proj",))

    def test_list_keys_empty(self):
        store = MagicMock()
        store.list_keys = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="list", target="keys",
            namespace=["empty"],
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 0)

    def test_list_keys_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="GET", target="keys",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /memory?target=namespaces — list_namespaces                           #
# ========================================================================= #


class TestListNamespaces(unittest.TestCase):
    def test_list_namespaces_all(self):
        store = MagicMock()
        store.list_namespaces = AsyncMock(return_value=[
            ("proj", "agent"),
            ("other",),
        ])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET", target="namespaces",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 2)
        self.assertIn(["proj", "agent"], parsed["data"]["namespaces"])
        self.assertIn(["other"], parsed["data"]["namespaces"])
        self.assertIsNone(parsed["data"]["prefix"])
        store.list_namespaces.assert_awaited_once_with(None)

    def test_list_namespaces_with_prefix(self):
        store = MagicMock()
        store.list_namespaces = AsyncMock(return_value=[("proj", "agent")])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET", target="namespaces",
            namespace=["proj"],
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["prefix"], ["proj"])
        store.list_namespaces.assert_awaited_once_with(("proj",))


# ========================================================================= #
# GET /memory?target=stats — stats                                          #
# ========================================================================= #


class TestStats(unittest.TestCase):
    def test_stats_returns_dict(self):
        store = MagicMock()
        store.get_stats = AsyncMock(return_value={
            "total_memories": 42,
            "total_namespaces": 5,
            "top_namespaces": [{"namespace": "/proj/agent", "count": 10}],
            "db_path": "/tmp/memories.db",
        })

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="GET", target="stats",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_memories"], 42)
        self.assertEqual(parsed["data"]["total_namespaces"], 5)
        store.get_stats.assert_awaited_once_with()


# ========================================================================= #
# DELETE /memory/{namespace}/{key} — delete single key                      #
# ========================================================================= #


class TestDeleteKey(unittest.TestCase):
    def test_delete_existing_key(self):
        store = MagicMock()
        store.delete = AsyncMock(return_value=True)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="delete",
            namespace=["proj"], key="mykey",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["deleted"])
        self.assertEqual(parsed["data"]["namespace"], ["proj"])
        self.assertEqual(parsed["data"]["key"], "mykey")
        self.assertIsNone(parsed["data"]["message"])
        store.delete.assert_awaited_once_with(("proj",), "mykey")

    def test_delete_nonexistent_key(self):
        store = MagicMock()
        store.delete = AsyncMock(return_value=False)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="DELETE",
            namespace=["proj"], key="missing",
        )))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["deleted"])
        self.assertEqual(parsed["data"]["message"], "Memory not found")

    def test_delete_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="delete",
            key="mykey",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())

    def test_delete_missing_key_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="delete",
            namespace=["proj"],
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("key", parsed["error"]["message"].lower())


# ========================================================================= #
# DELETE /memory?target=namespace — clear namespace                         #
# ========================================================================= #


class TestClearNamespace(unittest.TestCase):
    def test_clear_with_confirm(self):
        store = MagicMock()
        store.clear_namespace = AsyncMock(return_value=7)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="DELETE", target="namespace",
            namespace=["proj", "old"], confirm=True,
        )))
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["cleared"])
        self.assertEqual(parsed["data"]["namespace"], ["proj", "old"])
        self.assertEqual(parsed["data"]["deleted_count"], 7)
        store.clear_namespace.assert_awaited_once_with(("proj", "old"))

    def test_clear_via_friendly_verb(self):
        store = MagicMock()
        store.clear_namespace = AsyncMock(return_value=3)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="clear", target="namespace",
            namespace=["proj"], confirm=True,
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["deleted_count"], 3)

    def test_clear_without_confirm_returns_err(self):
        store = MagicMock()
        store.clear_namespace = AsyncMock(return_value=0)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="DELETE", target="namespace",
            namespace=["proj"],
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("confirm", parsed["error"]["message"].lower())
        # Critical: clear_namespace must NOT have been called.
        store.clear_namespace.assert_not_awaited()

    def test_clear_confirm_false_returns_err(self):
        store = MagicMock()
        store.clear_namespace = AsyncMock(return_value=0)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="DELETE", target="namespace",
            namespace=["proj"], confirm=False,
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("confirm", parsed["error"]["message"].lower())
        store.clear_namespace.assert_not_awaited()

    def test_clear_missing_namespace_returns_err(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="DELETE", target="namespace",
            confirm=True,
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("namespace", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported POST combinations                                             #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_invoke_not_implemented(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="POST", definer="INVOKE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_post_send_not_implemented(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="POST", definer="SEND",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not", parsed["error"]["message"].lower())

    def test_put_not_implemented(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="PUT", definer="REPLACE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())

    def test_patch_not_implemented(self):
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(),
            op="PATCH", definer="MODIFY",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())


# ========================================================================= #
# Legacy manage_memory op strings (backwards compatibility)                 #
# ========================================================================= #


class TestLegacyOpInterop(unittest.TestCase):
    """Verify memory-specific legacy op strings are mapped locally."""

    def test_legacy_list_keys_op(self):
        store = MagicMock()
        store.list_keys = AsyncMock(return_value=["a", "b"])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="list_keys",
            namespace=["proj"],
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["data"]["count"], 2)

    def test_legacy_list_namespaces_op(self):
        store = MagicMock()
        store.list_namespaces = AsyncMock(return_value=[("proj",)])

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="list_namespaces",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["data"]["count"], 1)

    def test_legacy_stats_op(self):
        store = MagicMock()
        store.get_stats = AsyncMock(return_value={"total_memories": 0})

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="stats",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["data"]["total_memories"], 0)

    def test_legacy_store_op(self):
        # Covered above via test_store_via_friendly_verb; add an explicit
        # test that "op=store" routes to POST+CREATE for clarity.
        store = MagicMock()
        store.store = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="store",
            namespace=["ns"], key="k", value="v",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")

    def test_legacy_clear_op_requires_confirm(self):
        store = MagicMock()
        store.clear_namespace = AsyncMock(return_value=0)

        # No confirm -> err, no side effect.
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="clear",
            namespace=["proj"],
        )))
        self.assertFalse(parsed["ok"])
        store.clear_namespace.assert_not_awaited()

        # With confirm -> cleared.
        parsed = json.loads(asyncio.run(memory(
            ctx=_make_ctx(memory_store=store),
            op="clear",
            namespace=["proj"], confirm=True,
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "DELETE")


if __name__ == "__main__":
    unittest.main()
