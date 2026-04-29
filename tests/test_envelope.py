"""Tests for SP2 response envelope, HEAD projection, and OPTIONS schema."""
import json
import unittest
from typing import ClassVar, Optional, List
from pydantic import BaseModel

from iterm_mcpy.responses import ok_envelope, err_envelope, project_head, options_schema


class Item(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    tags: List[str] = []

    HEAD_FIELDS: ClassVar[set[str]] = {"id", "name"}


class PydanticContainer(BaseModel):
    count: int
    items: List[Item] = []


class TestOkEnvelope(unittest.TestCase):
    def test_basic_shape(self):
        s = ok_envelope(method="GET", data={"count": 3})
        parsed = s
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 3)
        self.assertNotIn("error", parsed)

    def test_includes_definer_when_present(self):
        s = ok_envelope(method="POST", definer="CREATE", data={"id": "x"})
        parsed = s
        self.assertEqual(parsed["definer"], "CREATE")

    def test_omits_definer_when_none(self):
        s = ok_envelope(method="GET", data={"count": 3})
        parsed = s
        self.assertNotIn("definer", parsed)

    def test_serializes_pydantic_model(self):
        item = Item(id="a", name="Alpha", description="full")
        s = ok_envelope(method="GET", data=item)
        parsed = s
        self.assertEqual(parsed["data"]["id"], "a")
        self.assertEqual(parsed["data"]["description"], "full")

    def test_serializes_list_of_pydantic_models(self):
        items = [Item(id="a", name="Alpha"), Item(id="b", name="Bravo")]
        s = ok_envelope(method="GET", data=items)
        parsed = s
        self.assertEqual(len(parsed["data"]), 2)
        self.assertEqual(parsed["data"][0]["id"], "a")

    def test_excludes_none_from_pydantic(self):
        item = Item(id="a", name="Alpha")  # description is None
        s = ok_envelope(method="GET", data=item)
        parsed = s
        self.assertNotIn("description", parsed["data"])


class TestErrEnvelope(unittest.TestCase):
    def test_basic_shape(self):
        s = err_envelope(method="POST", error="boom")
        parsed = s
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["message"], "boom")
        self.assertNotIn("data", parsed)

    def test_includes_definer_when_present(self):
        s = err_envelope(method="POST", definer="CREATE", error="boom")
        parsed = s
        self.assertEqual(parsed["definer"], "CREATE")

    def test_omits_definer_when_none(self):
        s = err_envelope(method="POST", error="boom")
        parsed = s
        self.assertNotIn("definer", parsed)


class TestHeadProjection(unittest.TestCase):
    def test_single_item(self):
        item = Item(id="a", name="Alpha", description="full desc", tags=["x"])
        result = project_head(item)
        self.assertEqual(result, {"id": "a", "name": "Alpha"})

    def test_excludes_none_even_in_head_fields(self):
        class ItemWithOptional(BaseModel):
            id: str
            name: Optional[str] = None
            HEAD_FIELDS: ClassVar[set[str]] = {"id", "name"}

        item = ItemWithOptional(id="a")  # name is None
        result = project_head(item)
        self.assertEqual(result, {"id": "a"})

    def test_list_of_items(self):
        items = [Item(id="a", name="Alpha"), Item(id="b", name="Bravo")]
        result = project_head(items)
        self.assertEqual(
            result,
            [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Bravo"}],
        )

    def test_fallback_when_no_head_fields_declared(self):
        class Plain(BaseModel):
            key: str
            value: str
            extra: str

        item = Plain(key="k", value="v", extra="e")
        result = project_head(item)
        # Fallback: first two scalar fields
        self.assertEqual(set(result.keys()), {"key", "value"})

    def test_non_model_passes_through(self):
        # If someone hands us raw dicts or primitives, project_head is a no-op.
        self.assertEqual(project_head("raw-string"), "raw-string")
        self.assertEqual(project_head(42), 42)


class TestOptionsSchema(unittest.TestCase):
    def test_basic_schema(self):
        schema = options_schema(
            collection="items",
            methods={
                "GET": {"aliases": ["list"], "params": ["filter?"]},
                "HEAD": {"compact_fields": ["id", "name"]},
            },
        )
        self.assertEqual(schema["collection"], "items")
        self.assertIn("GET", schema["methods"])
        self.assertIn("HEAD", schema["methods"])

    def test_includes_sub_resources_when_present(self):
        schema = options_schema(
            collection="sessions",
            methods={"GET": {}},
            sub_resources=["output", "tags", "locks"],
        )
        self.assertEqual(schema["sub_resources"], ["output", "tags", "locks"])

    def test_omits_sub_resources_when_empty(self):
        schema = options_schema(
            collection="items",
            methods={"GET": {}},
        )
        self.assertNotIn("sub_resources", schema)


if __name__ == "__main__":
    unittest.main()
