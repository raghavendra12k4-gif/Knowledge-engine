import time

from app.core.config import settings
from app.core.db import connect, run_migrations
from app.core.watchdog import _InboxHandler, start_watchdog


def test_stable_inbox_file_creates_document_and_job(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    inbox = settings.inbox_dir
    inbox.mkdir(parents=True)
    source = inbox / "watched.md"
    source.write_text("# Watched")

    conn = connect(settings.db_path)
    run_migrations(conn)
    handler = _InboxHandler()
    handler._pending[str(source)] = (time.time() - 1, source.stat().st_size)
    try:
        handler.scan_pending()
        document = conn.execute(
            "SELECT id FROM documents WHERE title = 'watched.md'"
        ).fetchone()
        assert document is not None
        job = conn.execute(
            "SELECT id FROM jobs WHERE json_extract(payload, '$.document_id') = ?",
            (document["id"],),
        ).fetchone()
        assert job is not None
        assert source.exists()
    finally:
        conn.close()


def test_watchdog_stop_joins_observer_and_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    watcher = start_watchdog()
    assert watcher.scanner.is_alive()

    watcher.stop()

    assert not watcher.scanner.is_alive()
    assert not watcher.observer.is_alive()
