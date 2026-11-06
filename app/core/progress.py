import asyncio


class JobEventBroadcaster:
    """Pub/sub for job progress events used by SSE endpoints.

    Each job_id gets its own asyncio.Queue.  Handlers publish progress
    events; the SSE endpoint subscribes and streams them to the browser.
    """

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, job_id: int) -> asyncio.Queue:
        async with self._lock:
            if job_id not in self._queues:
                self._queues[job_id] = asyncio.Queue()
            return self._queues[job_id]

    async def publish(self, job_id: int, event: dict) -> None:
        async with self._lock:
            q = self._queues.get(job_id)
        if q is not None:
            await q.put(event)

    async def unsubscribe(self, job_id: int) -> None:
        async with self._lock:
            self._queues.pop(job_id, None)


broadcaster = JobEventBroadcaster()
