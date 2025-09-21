import asyncio
from typing import Any, Dict, Set


class EventBroker:
    """
    Simple in-memory pub/sub broker for broadcasting events to WebSocket subscribers.
    Each subscriber gets its own asyncio.Queue to avoid blocking others.
    """

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: Dict[str, Any]) -> None:
        # Fan-out to all subscribers; drop if their queue is full
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow subscriber; skip this event to avoid backpressure
                pass


event_broker = EventBroker()


