"""Event-driven flow control with decorators for reactive workflows.

Enables dynamic routing based on terminal output and agent events.
Inspired by CrewAI Flows and Agency Swarm patterns.

Usage:
    from core.flows import start, listen, router, trigger, EventBus, Flow

    class BuildDeployFlow(Flow):
        @start("build_requested")
        async def start_build(self, project: str):
            result = await self.run_build(project)
            await trigger("build_complete", result)

        @listen("build_complete")
        async def on_build_complete(self, result):
            if result.success:
                await trigger("deploy_requested", result)
            else:
                await trigger("build_failed", result)

        @router("deploy_requested")
        async def route_deploy(self, result) -> str:
            if result.environment == "production":
                return "production_deploy"
            return "staging_deploy"
"""

import asyncio
import functools
import inspect
import logging
import re
import time
import uuid
from abc import ABC
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Type,
    TypeVar,
)

logger = logging.getLogger(__name__)


# ============================================================================
# EVENT MODELS
# ============================================================================

class EventPriority(Enum):
    """Priority levels for event processing."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Event:
    """Represents an event in the flow system."""

    name: str
    payload: Any = None
    source: Optional[str] = None  # Agent or flow that triggered
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: EventPriority = EventPriority.NORMAL
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Event(name={self.name!r}, source={self.source!r}, id={self.id[:8]})"


@dataclass
class EventResult:
    """Result of processing an event."""

    event: Event
    success: bool
    handler_name: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    routed_to: Optional[str] = None


# ============================================================================
# LISTENER REGISTRY
# ============================================================================

@dataclass
class ListenerInfo:
    """Information about a registered listener."""

    event_name: str
    handler: Callable
    flow_class: Optional[Type] = None
    method_name: Optional[str] = None
    priority: EventPriority = EventPriority.NORMAL
    once: bool = False  # If True, unregister after first call
    condition: Optional[Callable[[Event], bool]] = None
    is_router: bool = False
    is_start: bool = False


class ListenerRegistry:
    """Coroutine-safe registry for event listeners."""

    def __init__(self):
        self._listeners: Dict[str, List[ListenerInfo]] = defaultdict(list)
        self._routers: Dict[str, ListenerInfo] = {}
        self._start_handlers: Dict[str, ListenerInfo] = {}
        self._lock = asyncio.Lock()

    async def register(self, listener: ListenerInfo) -> None:
        """Register a listener."""
        async with self._lock:
            if listener.is_router:
                if listener.event_name in self._routers:
                    logger.warning(
                        f"Overwriting existing router for event '{listener.event_name}'"
                    )
                self._routers[listener.event_name] = listener
            elif listener.is_start:
                if listener.event_name in self._start_handlers:
                    logger.warning(
                        f"Overwriting existing start handler for event '{listener.event_name}'"
                    )
                self._start_handlers[listener.event_name] = listener
            else:
                self._listeners[listener.event_name].append(listener)
                # Sort by priority (highest first)
                self._listeners[listener.event_name].sort(
                    key=lambda x: x.priority.value, reverse=True
                )

    async def unregister(self, event_name: str, handler: Callable) -> bool:
        """Unregister a specific handler."""
        async with self._lock:
            if event_name in self._listeners:
                original_len = len(self._listeners[event_name])
                self._listeners[event_name] = [
                    li for li in self._listeners[event_name]
                    if li.handler != handler
                ]
                return len(self._listeners[event_name]) < original_len
            return False

    async def get_listeners(self, event_name: str) -> List[ListenerInfo]:
        """Get all listeners for an event."""
        async with self._lock:
            return self._listeners.get(event_name, []).copy()

    async def get_router(self, event_name: str) -> Optional[ListenerInfo]:
        """Get router for an event."""
        async with self._lock:
            return self._routers.get(event_name)

    async def get_start_handler(self, event_name: str) -> Optional[ListenerInfo]:
        """Get start handler for an event."""
        async with self._lock:
            return self._start_handlers.get(event_name)

    async def get_all_event_names(self) -> Set[str]:
        """Get all registered event names."""
        async with self._lock:
            names = set(self._listeners.keys())
            names.update(self._routers.keys())
            names.update(self._start_handlers.keys())
            return names

    async def clear(self) -> None:
        """Clear all registrations."""
        async with self._lock:
            self._listeners.clear()
            self._routers.clear()
            self._start_handlers.clear()


# Global registry
_global_registry = ListenerRegistry()


# ============================================================================
# EVENT BUS
# ============================================================================

@dataclass
class _PatternSubscription:
    """Internal record for an active pattern subscription on the event bus."""
    subscription_id: str
    pattern: str
    wrapper: Callable[[str, str], Awaitable[bool]]
    event_name: Optional[str] = None
    target_session_id: Optional[str] = None
    target_agent: Optional[str] = None
    notify_agent: Optional[str] = None
    notify_level: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


class EventBus:
    """Central event bus for managing event flow.

    Supports:
    - Event routing with @router decorators
    - Multiple listeners per event with priority ordering
    - Conditional event handling
    - One-time listeners
    - Event history and replay
    - Integration with terminal monitoring
    """

    def __init__(
        self,
        registry: Optional[ListenerRegistry] = None,
        max_history: int = 1000,
        logger: Optional[logging.Logger] = None
    ):
        self._registry = registry or _global_registry
        self._history: List[EventResult] = []
        self._max_history = max_history
        self._lock = asyncio.Lock()
        self._logger = logger or logging.getLogger(__name__)
        self._running = False
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._process_task: Optional[asyncio.Task] = None
        self._flow_instances: Dict[str, "Flow"] = {}
        # Terminal output pattern subscriptions, keyed by subscription_id.
        self._pattern_subscriptions: Dict[str, "_PatternSubscription"] = {}

    async def start(self) -> None:
        """Start the event processing loop."""
        if self._running:
            return
        self._running = True
        self._process_task = asyncio.create_task(self._process_loop())
        self._logger.info("EventBus started")

    async def stop(self) -> None:
        """Stop the event processing loop."""
        self._running = False
        if self._process_task:
            # Check for unprocessed events
            pending_count = self._event_queue.qsize()
            if pending_count > 0:
                self._logger.warning(
                    f"EventBus stopping with {pending_count} unprocessed events in queue"
                )

            # Signal stop by putting a None event
            await self._event_queue.put(None)  # type: ignore
            try:
                await asyncio.wait_for(self._process_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._logger.warning("EventBus processing loop timed out during shutdown")
                self._process_task.cancel()
            self._process_task = None
        self._logger.info("EventBus stopped")

    async def _process_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                if event is None:  # Stop signal
                    break
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in event processing loop: {e}")

    async def trigger(
        self,
        event_name: str,
        payload: Any = None,
        source: Optional[str] = None,
        priority: EventPriority = EventPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        immediate: bool = False
    ) -> Optional[EventResult]:
        """Trigger an event.

        Args:
            event_name: Name of the event
            payload: Event payload data
            source: Source of the event (agent/flow name)
            priority: Event priority
            metadata: Additional metadata
            immediate: If True, process synchronously instead of queueing

        Returns:
            EventResult if immediate=True, None otherwise
        """
        event = Event(
            name=event_name,
            payload=payload,
            source=source,
            priority=priority,
            metadata=metadata or {}
        )

        if immediate:
            return await self._process_event(event)
        else:
            await self._event_queue.put(event)
            return None

    async def _process_event(
        self, event: Event, _visited_routes: Optional[Set[str]] = None
    ) -> EventResult:
        """Process a single event.

        Args:
            event: The event to process
            _visited_routes: Internal set tracking visited events to detect routing cycles
        """
        start_time = time.time()
        result = EventResult(event=event, success=True)

        # Initialize visited routes tracking for cycle detection
        if _visited_routes is None:
            _visited_routes = set()

        try:
            # Check for router first
            router = await self._registry.get_router(event.name)
            if router:
                routed_event_name = await self._call_handler(router, event)
                if routed_event_name and isinstance(routed_event_name, str):
                    # Check for routing cycle
                    if routed_event_name in _visited_routes:
                        self._logger.error(
                            f"Routing cycle detected: {event.name} -> {routed_event_name}. "
                            f"Visited: {_visited_routes}"
                        )
                        result.success = False
                        result.error = f"Routing cycle detected: {routed_event_name}"
                        return result

                    result.routed_to = routed_event_name
                    # Create new event with routed name
                    routed_event = Event(
                        name=routed_event_name,
                        payload=event.payload,
                        source=event.source,
                        priority=event.priority,
                        metadata={**event.metadata, "routed_from": event.name}
                    )
                    # Track this route and process routed event
                    _visited_routes.add(event.name)
                    return await self._process_event(routed_event, _visited_routes)

            # Get all listeners
            listeners = await self._registry.get_listeners(event.name)

            # Also check start handlers
            start_handler = await self._registry.get_start_handler(event.name)
            if start_handler:
                listeners = [start_handler] + listeners

            if not listeners:
                self._logger.debug(f"No listeners for event: {event.name}")
                result.success = True
                return result

            # Process listeners
            to_unregister = []
            errors = []
            handlers_called = []
            last_result = None

            for listener in listeners:
                # Check condition
                if listener.condition and not listener.condition(event):
                    continue

                try:
                    handler_result = await self._call_handler(listener, event)
                    handlers_called.append(listener.method_name)
                    last_result = handler_result

                    if listener.once:
                        to_unregister.append((listener.event_name, listener.handler))

                except Exception as e:
                    self._logger.error(
                        f"Handler {listener.method_name} failed for event {event.name}: {e}"
                    )
                    errors.append(f"{listener.method_name}: {e}")

            # Set result fields based on all handlers
            if handlers_called:
                result.handler_name = handlers_called[-1]  # Last handler called
                result.result = last_result
            if errors:
                result.success = False
                result.error = "; ".join(errors)

            # Unregister one-time listeners
            for event_name, handler in to_unregister:
                await self._registry.unregister(event_name, handler)

        except Exception as e:
            self._logger.error(f"Error processing event {event.name}: {e}")
            result.success = False
            result.error = str(e)

        finally:
            result.duration_ms = (time.time() - start_time) * 1000
            await self._add_to_history(result)

        return result

    async def _call_handler(self, listener: ListenerInfo, event: Event) -> Any:
        """Call a handler with the event."""
        handler = listener.handler

        # Get flow instance if this is a method
        instance = None
        if listener.flow_class:
            flow_key = listener.flow_class.__name__
            if flow_key in self._flow_instances:
                instance = self._flow_instances[flow_key]
            else:
                # Auto-instantiate flow with no arguments
                # Note: This bypasses user-defined __init__ parameters.
                # For custom initialization, register flows explicitly via
                # FlowManager.register_flow() or EventBus.register_flow()
                self._logger.debug(
                    f"Auto-instantiating flow {flow_key} with default constructor"
                )
                instance = listener.flow_class()
                instance._event_bus = self
                self._flow_instances[flow_key] = instance
                await instance.on_start()

        # Prepare arguments
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())

        # Skip 'self' if present
        if params and params[0] == "self":
            params = params[1:]

        # Call handler with appropriate arguments
        if instance:
            if len(params) == 0:
                result = handler(instance)
            elif len(params) == 1:
                result = handler(instance, event.payload)
            else:
                result = handler(instance, event)
        else:
            if len(params) == 0:
                result = handler()
            elif len(params) == 1:
                result = handler(event.payload)
            else:
                result = handler(event)

        # Await if coroutine
        if asyncio.iscoroutine(result):
            result = await result

        return result

    async def _add_to_history(self, result: EventResult) -> None:
        """Add event result to history."""
        async with self._lock:
            self._history.append(result)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    async def get_history(
        self,
        event_name: Optional[str] = None,
        limit: int = 100,
        success_only: bool = False
    ) -> List[EventResult]:
        """Get event history."""
        async with self._lock:
            history = self._history.copy()

        if event_name:
            history = [r for r in history if r.event.name == event_name]
        if success_only:
            history = [r for r in history if r.success]

        return history[-limit:]

    async def get_registered_events(self) -> List[str]:
        """Get list of all registered event names."""
        return list(await self._registry.get_all_event_names())

    # ------------------------------------------------------------------
    # Public introspection API
    #
    # These methods provide read-only visibility into event registrations
    # without exposing the internal ListenerRegistry. Tools (e.g. the
    # workflows OPTIONS inspector) should call these instead of reaching
    # into EventBus._registry directly.
    # ------------------------------------------------------------------

    async def get_listener_count(self, event_name: str) -> int:
        """Return the number of plain listeners registered for an event.

        Routers and start handlers are counted separately via
        :meth:`has_router` and :meth:`has_start_handler`. Returns 0 if the
        event is not registered.
        """
        listeners = await self._registry.get_listeners(event_name)
        return len(listeners)

    async def has_router(self, event_name: str) -> bool:
        """Return True if a @router handler is registered for an event."""
        router = await self._registry.get_router(event_name)
        return router is not None

    async def has_start_handler(self, event_name: str) -> bool:
        """Return True if a @start handler is registered for an event."""
        start_handler = await self._registry.get_start_handler(event_name)
        return start_handler is not None

    async def get_event_info(self, event_name: str) -> Dict[str, Any]:
        """Return an introspection snapshot for a single event.

        The returned dict is suitable for inclusion in an OPTIONS/inspect
        response. For unregistered events this returns a snapshot with
        zero listeners and no router/start handler.
        """
        listeners = await self._registry.get_listeners(event_name)
        router = await self._registry.get_router(event_name)
        start_handler = await self._registry.get_start_handler(event_name)
        return {
            "event_name": event_name,
            "listener_count": len(listeners),
            "has_listeners": len(listeners) > 0,
            "has_router": router is not None,
            "is_start_event": start_handler is not None,
        }

    def register_flow(self, flow_instance: "Flow") -> None:
        """Register a flow instance."""
        flow_key = flow_instance.__class__.__name__
        self._flow_instances[flow_key] = flow_instance
        flow_instance._event_bus = self

    async def subscribe_to_pattern(
        self,
        pattern: str,
        callback: Callable[[str, Any], Awaitable[None]],
        event_name: Optional[str] = None,
        target_session_id: Optional[str] = None,
        target_agent: Optional[str] = None,
        notify_agent: Optional[str] = None,
        notify_level: Optional[str] = None,
    ) -> str:
        """Subscribe to terminal output matching a pattern.

        Args:
            pattern: Regex pattern to match.
            callback: Async ``callback(matched_text, match_object)`` invoked
                on every match.
            event_name: Optional workflow event to trigger on match.
            target_session_id: If set, only fire when output came from this
                session. Combine with ``target_agent`` for cross-agent
                subscriptions ("watch agent X's pane for pattern Y").
            target_agent: If set, only fire when output came from a session
                owned by this agent. Resolved at match time.
            notify_agent: Stored as metadata so callers can identify which
                agent owns the subscription (for the agent-feed flow); the
                ``callback`` itself is responsible for actually pushing the
                notification.

        Returns:
            Subscription ID.
        """
        subscription_id = str(uuid.uuid4())

        async def wrapper(session_id: str, text: str) -> bool:
            """Returns True if pattern matched and was delivered."""
            if target_session_id is not None and session_id != target_session_id:
                return False
            match = re.search(pattern, text)
            if not match:
                return False
            await callback(match.group(0), match)
            if event_name:
                await self.trigger(
                    event_name,
                    payload={
                        "text": text,
                        "match": match.group(0),
                        "session_id": session_id,
                    },
                    source="pattern_subscription",
                )
            return True

        self._pattern_subscriptions[subscription_id] = _PatternSubscription(
            subscription_id=subscription_id,
            pattern=pattern,
            wrapper=wrapper,
            event_name=event_name,
            target_session_id=target_session_id,
            target_agent=target_agent,
            notify_agent=notify_agent,
            notify_level=notify_level,
        )
        self._logger.info(f"Registered pattern subscription: {pattern} (id={subscription_id})")
        return subscription_id

    async def unsubscribe_from_pattern(self, subscription_id: str) -> bool:
        """Cancel a pattern subscription. Returns True if removed."""
        sub = self._pattern_subscriptions.pop(subscription_id, None)
        if sub is None:
            return False
        self._logger.info(f"Unsubscribed from pattern: {sub.pattern} (id={subscription_id})")
        return True

    def list_pattern_subscriptions(self) -> List[Dict[str, Any]]:
        """Return metadata for active pattern subscriptions."""
        return [
            {
                "subscription_id": sub.subscription_id,
                "pattern": sub.pattern,
                "event_name": sub.event_name,
                "target_session_id": sub.target_session_id,
                "target_agent": sub.target_agent,
                "notify_agent": sub.notify_agent,
                "notify_level": sub.notify_level,
                "created_at": sub.created_at.isoformat(),
            }
            for sub in self._pattern_subscriptions.values()
        ]

    async def process_terminal_output(
        self,
        session_id: str,
        output: str,
        agent_name: Optional[str] = None,
    ) -> List[str]:
        """Process terminal output against pattern subscriptions.

        Subscriptions filtered by ``target_session_id`` only fire when
        ``session_id`` matches. Subscriptions filtered by ``target_agent``
        fire only when ``agent_name`` matches (caller resolves
        session→agent before dispatch).

        Returns list of triggered subscription IDs.
        """
        triggered = []
        # Snapshot to a list to allow callbacks that mutate
        # _pattern_subscriptions (e.g., self-unsubscribe).
        subs = list(self._pattern_subscriptions.values())
        for sub in subs:
            if sub.target_agent is not None and agent_name != sub.target_agent:
                continue
            try:
                matched = await sub.wrapper(session_id, output)
                if matched:
                    triggered.append(sub.subscription_id)
            except Exception as e:
                self._logger.error(f"Pattern callback error: {e}")
        return triggered

    async def clear(self) -> None:
        """Clear all registrations and history."""
        await self._registry.clear()
        async with self._lock:
            self._history.clear()
        self._flow_instances.clear()
        self._pattern_subscriptions.clear()


# Global event bus instance
_global_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


async def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance."""
    global _global_event_bus
    if _global_event_bus:
        await _global_event_bus.stop()
    _global_event_bus = bus


# ============================================================================
# DECORATORS
# ============================================================================

F = TypeVar("F", bound=Callable[..., Any])


def start(event_name: str = "workflow_start") -> Callable[[F], F]:
    """Mark function as workflow entry point.

    The decorated function will be called when the specified event is triggered.
    Only one function per event can be marked as a start handler.

    Args:
        event_name: Event that triggers this workflow start

    Example:
        @start("build_requested")
        async def start_build(self, project: str):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)

        # Store metadata on the function
        wrapper._is_start = True
        wrapper._start_event = event_name
        wrapper._original_func = func

        return wrapper  # type: ignore

    return decorator


def listen(
    event_name: str,
    priority: EventPriority = EventPriority.NORMAL,
    once: bool = False,
    condition: Optional[Callable[[Event], bool]] = None
) -> Callable[[F], F]:
    """Subscribe to an event.

    The decorated function will be called whenever the specified event is triggered.
    Multiple functions can listen to the same event.

    Args:
        event_name: Event to listen for
        priority: Handler priority (higher = called first)
        once: If True, unregister after first call
        condition: Optional condition function(event) -> bool

    Example:
        @listen("build_complete")
        async def on_build_complete(self, result):
            ...

        @listen("error", priority=EventPriority.HIGH)
        async def on_error(self, error):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)

        # Store metadata
        wrapper._is_listener = True
        wrapper._listen_event = event_name
        wrapper._priority = priority
        wrapper._once = once
        wrapper._condition = condition
        wrapper._original_func = func

        return wrapper  # type: ignore

    return decorator


def router(event_name: str) -> Callable[[F], F]:
    """Define routing logic for an event.

    The decorated function should return the name of the event to route to,
    or None to stop routing. Only one router per event is allowed.

    Args:
        event_name: Event to route

    Example:
        @router("deploy_requested")
        async def route_deploy(self, result) -> str:
            if result.environment == "production":
                return "production_deploy"
            return "staging_deploy"
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)

        # Store metadata
        wrapper._is_router = True
        wrapper._router_event = event_name
        wrapper._original_func = func

        return wrapper  # type: ignore

    return decorator


def on_output(
    pattern: str,
    event_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Trigger on terminal output matching a pattern.

    Args:
        pattern: Regex pattern to match against output
        event_name: Optional event to trigger on match

    Example:
        @on_output(r"error: (.+)", event_name="error_detected")
        async def on_error_output(self, match_text, match):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)

        # Store metadata
        wrapper._is_output_handler = True
        wrapper._output_pattern = pattern
        wrapper._output_event = event_name
        wrapper._original_func = func

        return wrapper  # type: ignore

    return decorator


# ============================================================================
# FLOW BASE CLASS
# ============================================================================

class Flow(ABC):
    """Base class for event-driven workflows.

    Subclass this to create reactive workflows with @start, @listen, and @router
    decorators. The flow will automatically register its handlers with the event bus.

    Example:
        class BuildDeployFlow(Flow):
            @start("build_requested")
            async def start_build(self, project: str):
                result = await self.run_build(project)
                await self.trigger("build_complete", result)

            @listen("build_complete")
            async def on_build_complete(self, result):
                if result.success:
                    await self.trigger("deploy_requested", result)
    """

    _event_bus: Optional[EventBus] = None

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus or get_event_bus()
        self._registered = False
        self._name = self.__class__.__name__
        self._logger = logging.getLogger(f"flow.{self._name}")

    async def register(self) -> None:
        """Register all handlers with the event bus."""
        if self._registered:
            return

        # Scan for decorated methods
        for name in dir(self):
            if name.startswith("_"):
                continue

            method = getattr(self, name)
            if not callable(method):
                continue

            # Check for decorators
            original = getattr(method, "_original_func", method)

            if getattr(method, "_is_start", False):
                listener = ListenerInfo(
                    event_name=method._start_event,
                    handler=original,
                    flow_class=self.__class__,
                    method_name=name,
                    is_start=True
                )
                await self._event_bus._registry.register(listener)
                self._logger.debug(f"Registered start handler: {name} -> {method._start_event}")

            elif getattr(method, "_is_listener", False):
                listener = ListenerInfo(
                    event_name=method._listen_event,
                    handler=original,
                    flow_class=self.__class__,
                    method_name=name,
                    priority=method._priority,
                    once=method._once,
                    condition=method._condition
                )
                await self._event_bus._registry.register(listener)
                self._logger.debug(f"Registered listener: {name} -> {method._listen_event}")

            elif getattr(method, "_is_router", False):
                listener = ListenerInfo(
                    event_name=method._router_event,
                    handler=original,
                    flow_class=self.__class__,
                    method_name=name,
                    is_router=True
                )
                await self._event_bus._registry.register(listener)
                self._logger.debug(f"Registered router: {name} -> {method._router_event}")

            elif getattr(method, "_is_output_handler", False):
                # Register pattern subscription
                # Capture 'original' with default argument to avoid closure bug
                await self._event_bus.subscribe_to_pattern(
                    pattern=method._output_pattern,
                    callback=lambda text, match, _orig=original: _orig(self, text, match),
                    event_name=method._output_event
                )
                self._logger.debug(f"Registered output handler: {name} -> {method._output_pattern}")

        self._event_bus.register_flow(self)
        self._registered = True
        self._logger.info(f"Flow {self._name} registered")

    async def trigger(
        self,
        event_name: str,
        payload: Any = None,
        priority: EventPriority = EventPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[EventResult]:
        """Trigger an event from this flow.

        Args:
            event_name: Name of the event to trigger
            payload: Event payload
            priority: Event priority
            metadata: Additional metadata

        Returns:
            EventResult if processed immediately, None if queued
        """
        return await self._event_bus.trigger(
            event_name=event_name,
            payload=payload,
            source=self._name,
            priority=priority,
            metadata=metadata
        )

    async def on_start(self) -> None:
        """Called when the flow is first instantiated. Override for initialization."""
        pass

    async def on_stop(self) -> None:
        """Called when the flow is stopped. Override for cleanup."""
        pass


# ============================================================================
# TRIGGER FUNCTION
# ============================================================================

async def trigger(
    event_name: str,
    payload: Any = None,
    source: Optional[str] = None,
    priority: EventPriority = EventPriority.NORMAL,
    metadata: Optional[Dict[str, Any]] = None,
    immediate: bool = False
) -> Optional[EventResult]:
    """Trigger an event, invoking all listeners.

    This is the main way to emit events in the flow system. Events are
    processed asynchronously unless immediate=True.

    Args:
        event_name: Name of the event
        payload: Event payload data
        source: Source of the event (agent/flow name)
        priority: Event priority for ordering
        metadata: Additional event metadata
        immediate: If True, process synchronously

    Returns:
        EventResult if immediate=True, None otherwise

    Example:
        # Trigger asynchronously (queued)
        await trigger("build_complete", {"success": True})

        # Trigger synchronously (immediate)
        result = await trigger("validation", data, immediate=True)
    """
    bus = get_event_bus()
    return await bus.trigger(
        event_name=event_name,
        payload=payload,
        source=source,
        priority=priority,
        metadata=metadata,
        immediate=immediate
    )


async def trigger_and_wait(
    event_name: str,
    payload: Any = None,
    source: Optional[str] = None,
    timeout: float = 30.0
) -> EventResult:
    """Trigger an event and wait for it to be processed.

    Args:
        event_name: Name of the event
        payload: Event payload
        source: Event source
        timeout: Max seconds to wait

    Returns:
        EventResult from processing

    Raises:
        asyncio.TimeoutError if timeout exceeded
    """
    result = await trigger(
        event_name=event_name,
        payload=payload,
        source=source,
        immediate=True
    )
    if result is None:
        raise RuntimeError("Immediate trigger returned None")
    return result


# ============================================================================
# FLOW MANAGER
# ============================================================================

class FlowManager:
    """Manages multiple flows and their lifecycle."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus or get_event_bus()
        self._flows: Dict[str, Flow] = {}
        self._logger = logging.getLogger(__name__)

    async def register_flow(self, flow: Flow) -> None:
        """Register a flow."""
        flow._event_bus = self._event_bus
        await flow.register()
        self._flows[flow._name] = flow
        self._logger.info(f"Registered flow: {flow._name}")

    async def register_flow_class(self, flow_class: Type[Flow]) -> Flow:
        """Register a flow class, instantiating it."""
        flow = flow_class(self._event_bus)
        await self.register_flow(flow)
        return flow

    async def unregister_flow(self, name: str) -> bool:
        """Unregister a flow by name."""
        if name in self._flows:
            flow = self._flows.pop(name)
            await flow.on_stop()
            return True
        return False

    def get_flow(self, name: str) -> Optional[Flow]:
        """Get a flow by name."""
        return self._flows.get(name)

    def list_flows(self) -> List[str]:
        """List all registered flow names."""
        return list(self._flows.keys())

    async def start(self) -> None:
        """Start the event bus."""
        await self._event_bus.start()

    async def stop(self) -> None:
        """Stop all flows and the event bus."""
        for flow in self._flows.values():
            await flow.on_stop()
        await self._event_bus.stop()

    async def trigger(
        self,
        event_name: str,
        payload: Any = None,
        **kwargs
    ) -> Optional[EventResult]:
        """Trigger an event."""
        return await self._event_bus.trigger(event_name, payload, **kwargs)


# Global flow manager
_global_flow_manager: Optional[FlowManager] = None


def get_flow_manager() -> FlowManager:
    """Get the global flow manager instance."""
    global _global_flow_manager
    if _global_flow_manager is None:
        _global_flow_manager = FlowManager()
    return _global_flow_manager


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def list_workflow_events() -> List[str]:
    """List all registered workflow events."""
    bus = get_event_bus()
    return await bus.get_registered_events()


async def get_event_history(
    event_name: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get event history as dictionaries."""
    bus = get_event_bus()
    results = await bus.get_history(event_name=event_name, limit=limit)
    return [
        {
            "event_name": r.event.name,
            "event_id": r.event.id,
            "source": r.event.source,
            "timestamp": r.event.timestamp.isoformat(),
            "success": r.success,
            "handler_name": r.handler_name,
            "routed_to": r.routed_to,
            "duration_ms": r.duration_ms,
            "error": r.error,
        }
        for r in results
    ]


# ============================================================================
# EXAMPLE: BUILD/DEPLOY WORKFLOW
# ============================================================================

@dataclass
class BuildResult:
    """Result of a build operation."""
    success: bool
    project: str
    version: str
    artifacts: List[str] = field(default_factory=list)
    error: Optional[str] = None
    environment: str = "staging"


@dataclass
class DeployResult:
    """Result of a deployment operation."""
    success: bool
    environment: str
    url: Optional[str] = None
    error: Optional[str] = None


class BuildDeployFlow(Flow):
    """Example flow for build and deploy workflow.

    Demonstrates the use of @start, @listen, @router, and @on_output decorators
    to create a reactive workflow that responds to events.

    Usage:
        # Register the flow
        flow_manager = get_flow_manager()
        await flow_manager.register_flow_class(BuildDeployFlow)

        # Trigger the workflow
        await trigger("build_requested", {"project": "my-app", "version": "1.0.0"})

    Events:
        - build_requested: Starts the build process
        - build_complete: Triggered when build finishes (routes to deploy)
        - build_failed: Triggered when build fails
        - deploy_requested: Starts the deployment process
        - production_deploy: Deploy to production
        - staging_deploy: Deploy to staging
        - deploy_complete: Triggered when deployment finishes
        - deploy_failed: Triggered when deployment fails
        - error_detected: Triggered when error pattern is detected in output
    """

    def __init__(self, event_bus: Optional[EventBus] = None):
        super().__init__(event_bus)
        self._current_build: Optional[BuildResult] = None
        self._current_deploy: Optional[DeployResult] = None

    @start("build_requested")
    async def start_build(self, payload: Dict[str, Any]) -> None:
        """Start the build process when build_requested event is received.

        Args:
            payload: Should contain 'project' and optionally 'version', 'environment'
        """
        project = payload.get("project", "unknown")
        version = payload.get("version", "0.0.1")
        environment = payload.get("environment", "staging")

        self._logger.info(f"Starting build for {project} v{version}")

        # Simulate build process - in real usage, this would run actual build commands
        # For now, we trigger build_complete after a short delay
        await asyncio.sleep(0.1)  # Simulated build time

        # Create build result
        result = BuildResult(
            success=True,
            project=project,
            version=version,
            artifacts=[f"{project}-{version}.tar.gz"],
            environment=environment
        )
        self._current_build = result

        # Trigger build complete event
        await self.trigger("build_complete", {
            "success": result.success,
            "project": result.project,
            "version": result.version,
            "artifacts": result.artifacts,
            "environment": result.environment
        })

    @listen("build_complete")
    async def on_build_complete(self, payload: Dict[str, Any]) -> None:
        """Handle successful build completion.

        Args:
            payload: Build result data
        """
        self._logger.info(f"Build complete: {payload.get('project')} v{payload.get('version')}")

        if payload.get("success"):
            # Trigger deployment
            await self.trigger("deploy_requested", payload)
        else:
            await self.trigger("build_failed", payload)

    @listen("build_failed", priority=EventPriority.HIGH)
    async def on_build_failed(self, payload: Dict[str, Any]) -> None:
        """Handle build failure.

        Args:
            payload: Build failure data
        """
        error = payload.get("error", "Unknown error")
        self._logger.error(f"Build failed: {error}")

        # Could trigger notifications, cleanup, etc.

    @router("deploy_requested")
    async def route_deploy(self, payload: Dict[str, Any]) -> str:
        """Route deployment to appropriate environment.

        Args:
            payload: Deployment request data with 'environment' field

        Returns:
            Event name to route to ('production_deploy' or 'staging_deploy')
        """
        environment = payload.get("environment", "staging")

        if environment == "production":
            self._logger.info("Routing to production deployment")
            return "production_deploy"
        else:
            self._logger.info("Routing to staging deployment")
            return "staging_deploy"

    @listen("staging_deploy")
    async def deploy_to_staging(self, payload: Dict[str, Any]) -> None:
        """Deploy to staging environment.

        Args:
            payload: Deployment data
        """
        project = payload.get("project", "unknown")
        version = payload.get("version", "0.0.1")

        self._logger.info(f"Deploying {project} v{version} to staging")

        # Simulate deployment
        await asyncio.sleep(0.1)

        result = DeployResult(
            success=True,
            environment="staging",
            url=f"https://staging.example.com/{project}"
        )
        self._current_deploy = result

        await self.trigger("deploy_complete", {
            "success": result.success,
            "environment": result.environment,
            "url": result.url,
            "project": project,
            "version": version
        })

    @listen("production_deploy")
    async def deploy_to_production(self, payload: Dict[str, Any]) -> None:
        """Deploy to production environment.

        Args:
            payload: Deployment data
        """
        project = payload.get("project", "unknown")
        version = payload.get("version", "0.0.1")

        self._logger.info(f"Deploying {project} v{version} to production")

        # Simulate deployment
        await asyncio.sleep(0.1)

        result = DeployResult(
            success=True,
            environment="production",
            url=f"https://example.com/{project}"
        )
        self._current_deploy = result

        await self.trigger("deploy_complete", {
            "success": result.success,
            "environment": result.environment,
            "url": result.url,
            "project": project,
            "version": version
        })

    @listen("deploy_complete")
    async def on_deploy_complete(self, payload: Dict[str, Any]) -> None:
        """Handle successful deployment.

        Args:
            payload: Deployment result data
        """
        environment = payload.get("environment", "unknown")
        url = payload.get("url", "N/A")
        self._logger.info(f"Deployment to {environment} complete: {url}")

    @listen("deploy_failed", priority=EventPriority.HIGH)
    async def on_deploy_failed(self, payload: Dict[str, Any]) -> None:
        """Handle deployment failure.

        Args:
            payload: Deployment failure data
        """
        error = payload.get("error", "Unknown error")
        self._logger.error(f"Deployment failed: {error}")

    @on_output(r"error:\s*(.+)", event_name="error_detected")
    async def on_error_output(self, text: str, match: Any) -> None:
        """Handle error patterns detected in terminal output.

        Args:
            text: The matched text
            match: The regex match object
        """
        error_message = match.group(1) if match.groups() else text
        self._logger.warning(f"Error detected in output: {error_message}")

    @on_output(r"BUILD\s+(SUCCESS|FAILED)", event_name="build_status_detected")
    async def on_build_status_output(self, text: str, match: Any) -> None:
        """Handle build status patterns in terminal output.

        Args:
            text: The matched text
            match: The regex match object
        """
        status = match.group(1) if match.groups() else "UNKNOWN"
        self._logger.info(f"Build status detected: {status}")

        if status == "FAILED" and self._current_build:
            await self.trigger("build_failed", {
                "project": self._current_build.project,
                "version": self._current_build.version,
                "error": "Build failed (detected from output)"
            })

    async def on_start(self) -> None:
        """Initialize the flow."""
        self._logger.info("BuildDeployFlow initialized")

    async def on_stop(self) -> None:
        """Clean up the flow."""
        self._logger.info("BuildDeployFlow stopped")
