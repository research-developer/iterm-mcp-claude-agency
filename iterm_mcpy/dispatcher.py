"""Base dispatcher for SP2 method-semantic collection tools.

Each collection tool subclasses MethodDispatcher and implements the
handlers it supports (on_get / on_post / on_patch / on_put / on_delete).

The dispatcher handles:
- Op normalization via resolve_op() (HTTP method or friendly verb)
- Auto-implemented OPTIONS (generated from the METHODS class attribute)
- Auto-implemented HEAD (calls on_get + projects via HEAD_FIELDS)
- Response envelope via ok_envelope / err_envelope
- Error handling for DefinerError, NotImplementedError, and generic exceptions
"""
from typing import Any, Optional

from core.definer_verbs import (
    DefinerError,
    DefinerResolution,
    resolve_op,
)
from iterm_mcpy.responses import (
    err_envelope,
    ok_envelope,
    options_schema,
    project_head,
)


class MethodDispatcher:
    """Base class for SP2 collection tools.

    Subclasses MUST set:
        - collection: str — name used in OPTIONS responses
        - METHODS: dict   — per-method metadata for OPTIONS generation

    Subclasses MAY set:
        - sub_resources: list[str] — child resource names

    Subclasses implement handlers for the methods they support. Unimplemented
    handlers raise NotImplementedError (the default), which the dispatcher
    converts into an err_envelope.
    """

    collection: str = ""
    METHODS: dict = {}
    sub_resources: list[str] = []

    async def on_get(self, ctx, **params) -> Any:
        raise NotImplementedError

    async def on_post(self, ctx, definer: str, **params) -> Any:
        raise NotImplementedError

    async def on_patch(self, ctx, definer: str, **params) -> Any:
        raise NotImplementedError

    async def on_put(self, ctx, definer: str, **params) -> Any:
        raise NotImplementedError

    async def on_delete(self, ctx, **params) -> Any:
        raise NotImplementedError

    async def dispatch(
        self,
        ctx,
        op: str,
        definer: Optional[str] = None,
        **params,
    ) -> str:
        """Entry point invoked by the MCP tool wrapper.

        Args:
            ctx: FastMCP Context (passed to handlers, may be None in tests).
            op: HTTP method or friendly verb.
            definer: Explicit definer for state-mutating methods (optional).
            **params: All other tool parameters, passed through to handlers.

        Returns:
            A JSON envelope string (see responses.ok_envelope / err_envelope).
        """
        try:
            resolution: DefinerResolution = resolve_op(op, definer)
        except DefinerError as e:
            return err_envelope(method=op.upper(), error=str(e))

        method = resolution.method
        resolved_definer = resolution.definer

        try:
            if method == "OPTIONS":
                return ok_envelope(
                    method="OPTIONS",
                    data=options_schema(
                        collection=self.collection,
                        methods=self.METHODS,
                        sub_resources=self.sub_resources,
                    ),
                )

            if method == "HEAD":
                # HEAD reuses GET and projects to HEAD_FIELDS.
                data = await self.on_get(ctx, **params)
                return ok_envelope(method="HEAD", data=project_head(data))

            if method == "GET":
                data = await self.on_get(ctx, **params)
                return ok_envelope(method="GET", data=data)

            if method == "POST":
                data = await self.on_post(ctx, definer=resolved_definer, **params)
                return ok_envelope(
                    method="POST", definer=resolved_definer, data=data
                )

            if method == "PATCH":
                data = await self.on_patch(ctx, definer=resolved_definer, **params)
                return ok_envelope(
                    method="PATCH", definer=resolved_definer, data=data
                )

            if method == "PUT":
                data = await self.on_put(ctx, definer=resolved_definer, **params)
                return ok_envelope(
                    method="PUT", definer=resolved_definer, data=data
                )

            if method == "DELETE":
                data = await self.on_delete(ctx, **params)
                return ok_envelope(method="DELETE", data=data)

            return err_envelope(
                method=method,
                error=f"Method {method} not supported on {self.collection}",
            )

        except NotImplementedError:
            return err_envelope(
                method=method,
                definer=resolved_definer,
                error=f"Method {method} not implemented on {self.collection}",
            )
        except Exception as e:
            return err_envelope(
                method=method,
                definer=resolved_definer,
                error=str(e),
            )
