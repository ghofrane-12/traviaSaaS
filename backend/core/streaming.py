# core/streaming.py
import asyncio
from contextvars import ContextVar
from typing import Optional

_sse_queue: ContextVar[Optional[asyncio.Queue]] = ContextVar("sse_queue", default=None)

def get_queue() -> Optional[asyncio.Queue]:
    return _sse_queue.get()

def set_queue(q: asyncio.Queue):
    _sse_queue.set(q)

async def emit(event: dict):
    """Appelé depuis n'importe quel nœud pour pousser un événement SSE."""
    q = _sse_queue.get()
    if q is not None:
        await q.put(event)