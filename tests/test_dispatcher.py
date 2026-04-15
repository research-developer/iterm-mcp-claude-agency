"""Tests for MethodDispatcher base class."""
import json
import unittest
from typing import ClassVar, List, Optional
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel

from iterm_mcpy.dispatcher import MethodDispatcher


class Item(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    HEAD_FIELDS: ClassVar[set[str]] = {"id", "name"}


class FakeCollection(MethodDispatcher):
    """Test double that captures what got called."""

    collection = "items"
    METHODS = {
        "GET":     {"aliases": ["list"], "params": ["filter?"]},
        "POST":    {"definers": {
                      "CREATE":  {"aliases": ["add"], "params": ["name"]},
                      "TRIGGER": {"aliases": ["launch"], "params": ["name"]},
                  }},
        "PATCH":   {"definers": {"MODIFY": {"aliases": ["update"], "params": ["id", "name?"]}}},
        "DELETE":  {"aliases": ["remove"], "params": ["id"]},
        "HEAD":    {"compact_fields": ["id", "name"]},
        "OPTIONS": {"description": "Discover schema"},
    }
    sub_resources = ["children"]

    def __init__(self):
        self.calls: list[tuple] = []

    async def on_get(self, ctx, **params):
        self.calls.append(("get", params))
        return [Item(id="a", name="Alpha", description="full"), Item(id="b", name="Bravo")]

    async def on_post(self, ctx, definer, **params):
        self.calls.append(("post", definer, params))
        return {"created": params.get("name", "unknown")}

    async def on_patch(self, ctx, definer, **params):
        self.calls.append(("patch", definer, params))
        return {"patched": params.get("id", "unknown")}

    async def on_delete(self, ctx, **params):
        self.calls.append(("delete", params))
        return {"deleted": params.get("id", "unknown")}


async def _dispatch(**kwargs):
    """Helper: run an async dispatch and return (parsed JSON, FakeCollection instance)."""
    import asyncio
    tool = FakeCollection()
    result = await tool.dispatch(ctx=None, **kwargs)
    return json.loads(result), tool


def run_async(coro):
    """Python 3.10+ test helper."""
    import asyncio
    return asyncio.run(coro)


class TestDispatchGet(unittest.TestCase):
    def test_op_list_routes_to_on_get(self):
        parsed, tool = run_async(_dispatch(op="list"))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(tool.calls[0][0], "get")

    def test_op_GET_method_routes_to_on_get(self):
        parsed, tool = run_async(_dispatch(op="GET"))
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(tool.calls[0][0], "get")

    def test_data_includes_full_fields(self):
        parsed, _ = run_async(_dispatch(op="list"))
        self.assertEqual(parsed["data"][0]["description"], "full")


class TestDispatchHead(unittest.TestCase):
    def test_head_calls_get_then_projects(self):
        parsed, tool = run_async(_dispatch(op="HEAD"))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])
        self.assertEqual(tool.calls[0][0], "get")  # HEAD reuses GET
        # Compact projection: no description
        self.assertNotIn("description", parsed["data"][0])
        self.assertEqual(set(parsed["data"][0].keys()), {"id", "name"})

    def test_head_verb_peek_works(self):
        parsed, _ = run_async(_dispatch(op="peek"))
        self.assertEqual(parsed["method"], "HEAD")


class TestDispatchPost(unittest.TestCase):
    def test_submit_verb_maps_to_post_create(self):
        # "submit" maps to POST+CREATE in the verb atlas
        parsed, tool = run_async(_dispatch(op="submit", name="x"))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertEqual(tool.calls[0], ("post", "CREATE", {"name": "x"}))

    def test_post_method_defaults_to_canonical(self):
        parsed, tool = run_async(_dispatch(op="POST", name="x"))
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertEqual(tool.calls[0][1], "CREATE")

    def test_post_with_explicit_definer(self):
        parsed, tool = run_async(_dispatch(op="POST", definer="TRIGGER", name="x"))
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertEqual(tool.calls[0][1], "TRIGGER")

    def test_fork_verb_maps_to_post_trigger(self):
        parsed, tool = run_async(_dispatch(op="fork", name="x"))
        self.assertEqual(parsed["definer"], "TRIGGER")


class TestDispatchPatch(unittest.TestCase):
    def test_update_verb_maps_to_patch_modify(self):
        parsed, tool = run_async(_dispatch(op="update", id="a"))
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")


class TestDispatchDelete(unittest.TestCase):
    def test_delete_verb(self):
        parsed, tool = run_async(_dispatch(op="delete", id="a"))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertNotIn("definer", parsed)
        self.assertEqual(tool.calls[0], ("delete", {"id": "a"}))

    def test_remove_verb_maps_to_delete(self):
        parsed, _ = run_async(_dispatch(op="remove", id="a"))
        self.assertEqual(parsed["method"], "DELETE")


class TestDispatchOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed, _ = run_async(_dispatch(op="OPTIONS"))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertEqual(parsed["data"]["collection"], "items")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertEqual(parsed["data"]["sub_resources"], ["children"])

    def test_schema_verb_works(self):
        parsed, _ = run_async(_dispatch(op="schema"))
        self.assertEqual(parsed["method"], "OPTIONS")


class TestDispatchErrors(unittest.TestCase):
    def test_unknown_verb_returns_err_envelope(self):
        parsed, _ = run_async(_dispatch(op="frobnicate"))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])

    def test_wrong_family_definer_returns_err_envelope(self):
        parsed, _ = run_async(_dispatch(op="POST", definer="REPLACE"))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"])

    def test_handler_exception_returns_err_envelope(self):
        class Broken(FakeCollection):
            async def on_get(self, ctx, **params):
                raise RuntimeError("oops")

        import asyncio
        tool = Broken()
        result = asyncio.run(tool.dispatch(ctx=None, op="list"))
        parsed = json.loads(result)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertEqual(parsed["error"], "oops")

    def test_not_implemented_returns_err_envelope(self):
        class NoPut(FakeCollection):
            async def on_put(self, ctx, definer, **params):
                raise NotImplementedError

        import asyncio
        tool = NoPut()
        result = asyncio.run(tool.dispatch(ctx=None, op="PUT", name="x"))
        parsed = json.loads(result)
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())


if __name__ == "__main__":
    unittest.main()
