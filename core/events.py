"""Registro acotado de eventos para el dashboard y sus clientes SSE."""

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AssistantEvent:
    event_id: int
    timestamp: str
    kind: str
    message: str


class EventHub:
    """Distribuye eventos recientes sin bloquear los workers productores."""

    def __init__(self, history_size: int = 100, subscriber_queue_size: int = 50):
        self._history: deque[AssistantEvent] = deque(maxlen=history_size)
        self._subscriber_queue_size = subscriber_queue_size
        self._subscribers: set[asyncio.Queue[AssistantEvent]] = set()
        self._next_id = 1

    async def publish(self, kind: str, message: str) -> None:
        event = AssistantEvent(
            event_id=self._next_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            message=message,
        )
        self._next_id += 1
        self._history.append(event)
        for subscriber in tuple(self._subscribers):
            if subscriber.full():
                try:
                    subscriber.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            subscriber.put_nowait(event)

    async def stream(self) -> AsyncIterator[str]:
        """Envía el historial disponible y luego eventos nuevos como SSE."""
        queue: asyncio.Queue[AssistantEvent] = asyncio.Queue(
            maxsize=self._subscriber_queue_size
        )
        self._subscribers.add(queue)
        try:
            for event in tuple(self._history):
                yield _format_sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _format_sse(event)
        finally:
            self._subscribers.discard(queue)


def _format_sse(event: AssistantEvent) -> str:
    data = json.dumps(asdict(event), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: log\ndata: {data}\n\n"


event_hub = EventHub()
_core_running = False


def set_core_running(running: bool) -> None:
    """Actualiza el estado del worker principal del asistente."""
    global _core_running
    _core_running = running


def is_core_running() -> bool:
    return _core_running
