"""Tests for the public EventBus introspection API.

These tests exercise the public introspection methods added to satisfy
the PR #114 review: tools must not reach into ``EventBus._registry``.
"""

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


@pytest.mark.asyncio
async def test_get_listener_count_unregistered_returns_zero(event_bus: EventBus):
    assert await event_bus.get_listener_count("nope") == 0


@pytest.mark.asyncio
async def test_has_router_unregistered_returns_false(event_bus: EventBus):
    assert await event_bus.has_router("nope") is False


@pytest.mark.asyncio
async def test_has_start_handler_unregistered_returns_false(event_bus: EventBus):
    assert await event_bus.has_start_handler("nope") is False


@pytest.mark.asyncio
async def test_get_event_info_unregistered_returns_empty_snapshot(event_bus: EventBus):
    info = await event_bus.get_event_info("nope")
    assert info == {
        "event_name": "nope",
        "listener_count": 0,
        "has_listeners": False,
        "has_router": False,
        "is_start_event": False,
    }


@pytest.mark.asyncio
async def test_get_listener_count_counts_plain_listeners(event_bus: EventBus):
    await event_bus._registry.register(_make_listener("built"))
    await event_bus._registry.register(_make_listener("built"))
    await event_bus._registry.register(_make_listener("built"))

    assert await event_bus.get_listener_count("built") == 3


@pytest.mark.asyncio
async def test_has_router_detects_registered_router(event_bus: EventBus):
    await event_bus._registry.register(_make_listener("routed", is_router=True))

    assert await event_bus.has_router("routed") is True
    # Router should not be counted as a plain listener.
    assert await event_bus.get_listener_count("routed") == 0
    assert await event_bus.has_start_handler("routed") is False


@pytest.mark.asyncio
async def test_has_start_handler_detects_start(event_bus: EventBus):
    await event_bus._registry.register(_make_listener("kickoff", is_start=True))

    assert await event_bus.has_start_handler("kickoff") is True
    # Start handler should not be counted as a plain listener.
    assert await event_bus.get_listener_count("kickoff") == 0
    assert await event_bus.has_router("kickoff") is False


@pytest.mark.asyncio
async def test_get_event_info_combines_all_registrations(event_bus: EventBus):
    await event_bus._registry.register(_make_listener("combo"))
    await event_bus._registry.register(_make_listener("combo"))
    await event_bus._registry.register(_make_listener("combo", is_router=True))
    await event_bus._registry.register(_make_listener("combo", is_start=True))

    info = await event_bus.get_event_info("combo")
    assert info == {
        "event_name": "combo",
        "listener_count": 2,
        "has_listeners": True,
        "has_router": True,
        "is_start_event": True,
    }


@pytest.mark.asyncio
async def test_introspection_does_not_mutate_registry(event_bus: EventBus):
    await event_bus._registry.register(_make_listener("stable"))

    # Call each introspection method.
    await event_bus.get_listener_count("stable")
    await event_bus.has_router("stable")
    await event_bus.has_start_handler("stable")
    await event_bus.get_event_info("stable")

    # Registry state should be unchanged.
    assert await event_bus.get_listener_count("stable") == 1
    assert await event_bus.has_router("stable") is False
    assert await event_bus.has_start_handler("stable") is False
