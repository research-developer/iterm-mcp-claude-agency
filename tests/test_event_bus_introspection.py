"""Tests for the public EventBus introspection API.

These tests exercise the public introspection methods added to satisfy
the PR #114 review: tools must not reach into ``EventBus._registry``.

Driven via ``asyncio.run`` rather than pytest-asyncio so the test runs in
the project's pytest config without an extra plugin.
"""

import asyncio

import pytest

from core.flows import (
    EventBus,
    EventPriority,
    ListenerInfo,
    ListenerRegistry,
)


def _make_listener(
    event_name: str,
    *,
    is_router: bool = False,
    is_start: bool = False,
    priority: EventPriority = EventPriority.NORMAL,
) -> ListenerInfo:
    """Build a minimal ListenerInfo for registration."""

    async def _handler(event):  # pragma: no cover - not invoked
        return None

    return ListenerInfo(
        event_name=event_name,
        handler=_handler,
        priority=priority,
        is_router=is_router,
        is_start=is_start,
    )


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus with its own isolated registry."""
    return EventBus(registry=ListenerRegistry())


def _run(coro):
    return asyncio.run(coro)


def test_get_listener_count_unregistered_returns_zero(event_bus: EventBus):
    assert _run(event_bus.get_listener_count("nope")) == 0


def test_has_router_unregistered_returns_false(event_bus: EventBus):
    assert _run(event_bus.has_router("nope")) is False


def test_has_start_handler_unregistered_returns_false(event_bus: EventBus):
    assert _run(event_bus.has_start_handler("nope")) is False


def test_get_event_info_unregistered_returns_empty_snapshot(event_bus: EventBus):
    info = _run(event_bus.get_event_info("nope"))
    assert info == {
        "event_name": "nope",
        "listener_count": 0,
        "has_listeners": False,
        "has_router": False,
        "is_start_event": False,
    }


def test_get_listener_count_counts_plain_listeners(event_bus: EventBus):
    async def _setup_and_check():
        await event_bus._registry.register(_make_listener("built"))
        await event_bus._registry.register(_make_listener("built"))
        await event_bus._registry.register(_make_listener("built"))
        return await event_bus.get_listener_count("built")

    assert _run(_setup_and_check()) == 3


def test_has_router_detects_registered_router(event_bus: EventBus):
    async def _setup_and_check():
        await event_bus._registry.register(_make_listener("routed", is_router=True))
        return (
            await event_bus.has_router("routed"),
            await event_bus.get_listener_count("routed"),
            await event_bus.has_start_handler("routed"),
        )

    has_router, count, has_start = _run(_setup_and_check())
    assert has_router is True
    # Router should not be counted as a plain listener.
    assert count == 0
    assert has_start is False


def test_has_start_handler_detects_start(event_bus: EventBus):
    async def _setup_and_check():
        await event_bus._registry.register(_make_listener("kickoff", is_start=True))
        return (
            await event_bus.has_start_handler("kickoff"),
            await event_bus.get_listener_count("kickoff"),
            await event_bus.has_router("kickoff"),
        )

    has_start, count, has_router = _run(_setup_and_check())
    assert has_start is True
    # Start handler should not be counted as a plain listener.
    assert count == 0
    assert has_router is False


def test_get_event_info_combines_all_registrations(event_bus: EventBus):
    async def _setup_and_check():
        await event_bus._registry.register(_make_listener("combo"))
        await event_bus._registry.register(_make_listener("combo"))
        await event_bus._registry.register(_make_listener("combo", is_router=True))
        await event_bus._registry.register(_make_listener("combo", is_start=True))
        return await event_bus.get_event_info("combo")

    info = _run(_setup_and_check())
    assert info == {
        "event_name": "combo",
        "listener_count": 2,
        "has_listeners": True,
        "has_router": True,
        "is_start_event": True,
    }


def test_introspection_does_not_mutate_registry(event_bus: EventBus):
    async def _setup_call_and_verify():
        await event_bus._registry.register(_make_listener("stable"))
        # Call each introspection method.
        await event_bus.get_listener_count("stable")
        await event_bus.has_router("stable")
        await event_bus.has_start_handler("stable")
        await event_bus.get_event_info("stable")
        # Registry state should be unchanged.
        return (
            await event_bus.get_listener_count("stable"),
            await event_bus.has_router("stable"),
            await event_bus.has_start_handler("stable"),
        )

    count, has_router, has_start = _run(_setup_call_and_verify())
    assert count == 1
    assert has_router is False
    assert has_start is False
