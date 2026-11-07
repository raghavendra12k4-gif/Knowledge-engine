import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.core.db import connect, get_db
from app.core.jobs import get_job
from app.core.progress import broadcaster

router = APIRouter()

_TERMINAL_EVENT_KINDS = {"completed", "failed"}
_TERMINAL_JOB_STATUSES = {"done", "failed", "cancelled"}


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    """Server-Sent Events stream for a job's progress.

    Every event's `data` is a JSON string. The stream sends the current state
    first, then live events, and ends after a terminal (completed/failed) event.
    """
    if get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_generator():
        # Subscribe BEFORE reading the state so an event that fires in between
        # is queued instead of lost.
        queue = await broadcaster.subscribe(job_id)
        try:
            conn = connect()
            try:
                job = get_job(conn, job_id)
            finally:
                conn.close()
            if job is None:
                return

            yield {
                "event": "state",
                "data": json.dumps(
                    {"id": job_id, "status": job["status"], "progress": job["progress"]}
                ),
            }
            if job["status"] in _TERMINAL_JOB_STATUSES:
                return

            while True:
                event = await queue.get()
                yield {"event": event["kind"], "data": json.dumps(event, default=str)}
                if event["kind"] in _TERMINAL_EVENT_KINDS:
                    return
        finally:
            await broadcaster.unsubscribe(job_id)

    return EventSourceResponse(event_generator())