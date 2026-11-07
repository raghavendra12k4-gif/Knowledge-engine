import sqlite3

from fastapi import APIRouter, Depends

from app.core.db import get_db

router = APIRouter()


@router.get("/health")
def health(db: sqlite3.Connection = Depends(get_db)) -> dict:
    version = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return {"status": "ok", "schema_version": version}
