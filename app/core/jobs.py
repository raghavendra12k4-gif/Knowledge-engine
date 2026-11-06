import asyncio
import json
import sqlite3
from datetime import UTC, datetime

from app.core.db import connect
from app.core.progress import broadcaster


def _now() -> str:
    return datetime.now(UTC).isoformat()


_VALID_JOB_TYPES = {"ingest"}


def create_job(conn: sqlite3.Connection, *, type: str, payload: dict) -> int:
    if type not in _VALID_JOB_TYPES:
        raise ValueError(f"unknown job type: {type}")
    cur = conn.execute(
        """INSERT INTO jobs (type, status, progress, payload, created_at, updated_at)
           VALUES (?, 'queued', 0, ?, ?, ?)""",
        (type, json.dumps(payload), _now(), _now()),
    )
    conn.commit()
    return cur.lastrowid


def claim_job(conn: sqlite3.Connection) -> dict | None:
    """Atomically claim one queued job. Returns the job row or None."""
    row = conn.execute(
        """SELECT * FROM jobs
           WHERE status = 'queued'
           ORDER BY created_at ASC
           LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    now = _now()
    conn.execute(
        "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
        (now, row["id"]),
    )
    conn.commit()
    return dict(row, status="running", updated_at=now)


def update_progress(conn: sqlite3.Connection, job_id: int, progress: float) -> None:
    """Update a job's progress (0.0 to 100.0) and publish an SSE event."""
    conn.execute(
        "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
        (progress, _now(), job_id),
    )
    conn.commit()
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            broadcaster.publish(job_id, {"kind": "progress", "progress": progress})
        )
    except RuntimeError:
        pass  # no running event loop (sync context)


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    now = _now()
    status = "failed" if error else "done"
    progress = 100.0 if not error else None
    conn.execute(
        """UPDATE jobs SET status = ?, result = ?, error = ?, updated_at = ?,
                   progress = COALESCE(?, progress)
           WHERE id = ?""",
        (status, json.dumps(result) if result else None, error, now, progress, job_id),
    )
    conn.commit()
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            broadcaster.publish(
                job_id,
                {
                    "kind": "completed" if not error else "failed",
                    "status": status,
                    "error": error,
                    "result": result,
                },
            )
        )
    except RuntimeError:
        pass  # no running event loop (sync context)


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return None if row is None else dict(row)


def requeue_stale_jobs(conn: sqlite3.Connection) -> list[int]:
    """Re-queue jobs left running from a previous run."""
    rows = conn.execute("SELECT id FROM jobs WHERE status = 'running'").fetchall()
    now = _now()
    ids = [row["id"] for row in rows]
    for job_id in ids:
        conn.execute(
            "UPDATE jobs SET status = 'queued', progress = 0, updated_at = ? WHERE id = ?",
            (now, job_id),
        )
    conn.commit()
    return ids


async def run_worker(
    handlers: dict,
    poll_interval: float = 1.0,
    concurrency: int = 2,
) -> None:
    """Asyncio job worker. Runs until cancelled.

    Polls the jobs table, claims queued jobs, dispatches to the matching
    handler, and marks the job done or failed.  Handlers may be sync or
    async functions.
    """
    while True:
        conn = connect()
        try:
            job = claim_job(conn)
        finally:
            conn.close()

        if job is None:
            await asyncio.sleep(poll_interval)
            continue

        job_id = job["id"]
        handler = handlers.get(job["type"])
        if handler is None:
            conn = connect()
            try:
                complete_job(conn, job_id, error=f"unknown job type: {job['type']}")
            finally:
                conn.close()
            continue

        try:
            payload = json.loads(job["payload"] or "{}")
            await broadcaster.publish(
                job_id, {"kind": "started", "status": "running"}
            )
            if asyncio.iscoroutinefunction(handler):
                result = await handler(payload)
            else:
                result = handler(payload)
            conn = connect()
            try:
                complete_job(conn, job_id, result=result)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - job must never crash the worker
            conn = connect()
            try:
                complete_job(conn, job_id, error=str(exc))
            finally:
                conn.close()
