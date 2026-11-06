import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import connect, init_db
from app.core.jobs import requeue_stale_jobs, run_worker
from app.core.watchdog import start_watchdog
from app.routers import documents, health, jobs
from app.services.ingest import handle_ingest_job


@asynccontextmanager
async def lifespan(app: FastAPI):
    for d in (settings.originals_dir, settings.inbox_dir, settings.pages_dir):
        d.mkdir(parents=True, exist_ok=True)

    init_db()

    conn = connect()
    try:
        requeue_stale_jobs(conn)
    finally:
        conn.close()

    handlers = {"ingest": handle_ingest_job}
    worker_task = asyncio.create_task(run_worker(handlers))

    observer = start_watchdog()

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    observer.stop()
    observer.join()


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge Engine", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.dev_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    return app


app = create_app()
