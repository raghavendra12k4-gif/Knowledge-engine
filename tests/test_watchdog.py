import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import connect, run_migrations
from app.core.watchdog import _InboxHandler, start_watchdog
from app.main import create_app


def _wait_until(predicate, timeout=20.0, interval=0.1):
    """Poll until predicate() returns something truthy; fail the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _doc(client, title, status):
    for doc in client.get("/api/documents").json()["documents"]:
        if doc["title"] == title and doc["status"] == status:
            return doc
    return None


def _rejected_names():
    rejected = settings.inbox_dir / "_rejected"
    return [p.name for p in rejected.iterdir()] if rejected.exists() else []


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
            "SELECT id, payload FROM jobs WHERE json_extract(payload, '$.document_id') = ?",
            (document["id"],),
        ).fetchone()
        assert job is not None
        # The job must carry the inbox path so the handler can clean up afterwards.
        payload = json.loads(job["payload"])
        assert payload["inbox_path"] == str(source)
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


def test_touch_ignores_dotfiles_and_files_outside_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    inbox = settings.inbox_dir
    (inbox / "_rejected").mkdir(parents=True)

    rejected_file = inbox / "_rejected" / "bad.pdf"
    rejected_file.write_bytes(b"x")
    dotfile = inbox / ".upload_abc.md"
    dotfile.write_text("x")
    normal = inbox / "ok.md"
    normal.write_text("x")

    handler = _InboxHandler()
    handler._touch(str(rejected_file))
    handler._touch(str(dotfile))
    assert handler._pending == {}

    handler._touch(str(normal))
    assert list(handler._pending) == [str(normal)]


def test_scan_existing_queues_only_regular_inbox_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    inbox = settings.inbox_dir
    (inbox / "_rejected").mkdir(parents=True)
    (inbox / "a.md").write_text("a")
    (inbox / ".hidden").write_text("h")
    (inbox / "_rejected" / "old.pdf").write_bytes(b"x")

    handler = _InboxHandler()
    handler.scan_existing()

    assert list(handler._pending) == [str(inbox / "a.md")]


def test_startup_scan_ingests_files_dropped_while_app_was_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.inbox_dir.mkdir(parents=True)
    offline = settings.inbox_dir / "offline.md"
    offline.write_text("# dropped while the app was off")

    with TestClient(create_app()) as client:
        _wait_until(lambda: _doc(client, "offline.md", "ready") and not offline.exists())


def test_inbox_file_end_to_end_ready_and_removed(client):
    dropped = settings.inbox_dir / "dropped.md"
    dropped.write_text("# hello from the inbox")

    # Observer event -> debounce -> ingest -> worker -> ready -> file removed.
    _wait_until(lambda: _doc(client, "dropped.md", "ready") and not dropped.exists())


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("corrupt.pdf", b"%PDF-1.4\nnot a real PDF"),
        ("hostile.pdf", b"MZ\x90\x00\x03fake executable renamed to .pdf"),
    ],
)
def test_corrupt_inbox_file_fails_and_lands_in_rejected(client, name, content):
    bad = settings.inbox_dir / name
    bad.write_bytes(content)

    stem = name.rsplit(".", 1)[0]

    def failed_and_moved():
        return (
            _doc(client, name, "failed")
            and not bad.exists()
            and any(n.startswith(stem) for n in _rejected_names())
        )

    _wait_until(failed_and_moved)
    failed = _doc(client, name, "failed")
    assert failed is not None and "error" in failed and failed["error"]

    # The worker must still be alive for the next file.
    healthy = settings.inbox_dir / "after-failure.md"
    healthy.write_text("still working")
    _wait_until(lambda: _doc(client, "after-failure.md", "ready") and not healthy.exists())