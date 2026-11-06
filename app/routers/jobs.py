import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.core.db import get_db
from app.core.jobs import get_job
from app.core.progress import broadcaster

router = APIRouter()


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    """Server-Sent Events stream for a job's progress.

    The client connects here to receive live progress updates as the
    job moves through queued → processing → ready | failed.
    """
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_generator():
        queue = await broadcaster.subscribe(job_id)
        try:
            # Send the current job state immediately.
            yield {
                "event": "state",
                "data": {
                    "id": job_id,
                    "status": job["status"],
                    "progress": job["progress"],
                },
            }
            # Stream live events until the client disconnects.
            while True:
                event = await queue.get()
                yield {"event": event["kind"], "data": event}
        finally:
            await broadcaster.unsubscribe(job_id)

    return EventSourceResponse(event_generator())
