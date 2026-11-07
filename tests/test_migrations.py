from app.core.db import connect, run_migrations


def test_migrations_apply_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert run_migrations(conn) == [1, 2, 3, 4]
    assert run_migrations(conn) == []  # idempotent

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"schema_version", "settings", "audit_log"} <= tables


def test_pragmas(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
