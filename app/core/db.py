import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from app.core.config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d+)_.+\.sql$")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def run_migrations(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply numbered .sql files not yet recorded in schema_version. Returns applied versions."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.commit()
    done = {row["version"] for row in conn.execute("SELECT version FROM schema_version")}

    files = []
    for f in directory.glob("*.sql"):
        m = _MIGRATION_NAME.match(f.name)
        if m:
            files.append((int(m.group(1)), f))
    files.sort()

    applied: list[int] = []
    for version, f in files:
        if version in done:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_version (version, name) VALUES (?, ?)", (version, f.name)
        )
        conn.commit()
        applied.append(version)
    return applied


def init_db(path: Path | None = None) -> list[int]:
    conn = connect(path)
    try:
        return run_migrations(conn)
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
