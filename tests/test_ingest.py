import asyncio
import hashlib
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.db import connect, run_migrations
from app.core.jobs import (
    claim_job,
    complete_job,
    create_job,
    get_job,
    requeue_stale_jobs,
)
from app.core.progress import broadcaster
from app.services.documents import DuplicateDocumentError
from app.services.ingest import (
    IngestError,
    classify_kind,
    copy_original,
    detect_kind_by_magic,
    hash_file,
    ingest_file,
    move_to_rejected,
    remove_from_inbox,
)

# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------

def test_hash_file_deterministic(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello world")
    h1 = hash_file(p)
    h2 = hash_file(p)
    assert h1 == h2
    assert h1 == hashlib.sha256(b"hello world").hexdigest()


def test_hash_file_different_content(tmp_path):
    p1 = tmp_path / "a.txt"
    p2 = tmp_path / "b.txt"
    p1.write_text("aaa")
    p2.write_text("bbb")
    assert hash_file(p1) != hash_file(p2)


# ---------------------------------------------------------------------------
# detect_kind_by_magic
# ---------------------------------------------------------------------------

def test_detect_pdf_magic(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\nfake content")
    assert detect_kind_by_magic(p) == "pdf"


def test_detect_docx_magic(tmp_path):
    p = tmp_path / "x.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    assert detect_kind_by_magic(p) == "docx"


def test_detect_png_magic(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert detect_kind_by_magic(p) == "image"


def test_detect_jpg_magic(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    assert detect_kind_by_magic(p) == "image"


def test_detect_webp_magic(tmp_path):
    p = tmp_path / "x.webp"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100)
    assert detect_kind_by_magic(p) == "image"


def test_detect_html_magic(tmp_path):
    p = tmp_path / "x.html"
    p.write_bytes(b"<!DOCTYPE html><html><body>hi</body></html>")
    assert detect_kind_by_magic(p) == "web"


def test_detect_text_magic(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello world\n")
    assert detect_kind_by_magic(p) == "text"


def test_detect_corrupt_renamed_pdf(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"MZ\x90\x00\x03fake exe content")
    assert detect_kind_by_magic(p) is None


def test_detect_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    assert detect_kind_by_magic(p) == "text"


# ---------------------------------------------------------------------------
# classify_kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext,kind", [
    (".md", "text"),
    (".txt", "text"),
    (".pdf", "pdf"),
    (".docx", "docx"),
    (".png", "image"),
    (".jpg", "image"),
    (".jpeg", "image"),
    (".webp", "image"),
    (".html", "web"),
    (".htm", "web"),
    (".xyz", "text"),
])
def test_classify_kind(ext, kind):
    assert classify_kind(Path(f"file{ext}")) == kind


# ---------------------------------------------------------------------------
# ingest_file pipeline
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn(tmp_path):
    c = connect(tmp_path / "t.db")
    run_migrations(c)
    yield c
    c.close()


def test_ingest_file_creates_document_and_job(tmp_path, db_conn):
    p = tmp_path / "notes.md"
    p.write_text("# Hello\n\nThis is a test note.")
    result = ingest_file(p, db_conn)

    doc = result["document"]
    assert doc["title"] == "notes.md"
    assert doc["kind"] == "text"
    assert doc["status"] == "queued"
    assert doc["file_hash"] is not None

    job = get_job(db_conn, result["job_id"])
    assert job is not None
    assert job["type"] == "ingest"
    assert job["status"] == "queued"

    assert Path(doc["path"]).exists()
    assert doc["path"].endswith(".md")


def test_ingest_duplicate_hash_raises(tmp_path, db_conn):
    p1 = tmp_path / "a.md"
    p2 = tmp_path / "b.md"
    content = b"same content here"
    p1.write_bytes(content)
    p2.write_bytes(content)

    ingest_file(p1, db_conn)
    with pytest.raises(DuplicateDocumentError):
        ingest_file(p2, db_conn)


def test_ingest_corrupt_file_raises(tmp_path, db_conn):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"MZ\x90\x00\x03fake exe")
    with pytest.raises(IngestError):
        ingest_file(p, db_conn)


# ---------------------------------------------------------------------------
# copy_original / remove_from_inbox / move_to_rejected
# ---------------------------------------------------------------------------

def test_copy_original_idempotent(tmp_path):
    src = tmp_path / "x.txt"
    src.write_text("data")
    file_hash = hash_file(src)

    dest1 = copy_original(src, file_hash)
    dest2 = copy_original(src, file_hash)
    assert dest1 == dest2
    assert dest1.exists()
    assert dest1.read_text() == "data"


def test_remove_from_inbox_missing_ok(tmp_path):
    p = tmp_path / "gone.txt"
    remove_from_inbox(p)


def test_move_to_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Settings, "inbox_dir", property(lambda self: tmp_path)
    )
    src = tmp_path / "bad.pdf"
    src.write_bytes(b"garbage")
    move_to_rejected(src)
    assert not src.exists()
    rejected_files = list((tmp_path / "_rejected").iterdir())
    assert len(rejected_files) == 1


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def test_job_lifecycle(db_conn):
    job_id = create_job(db_conn, type="ingest", payload={"x": 1})
    job = get_job(db_conn, job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["progress"] == 0

    claimed = claim_job(db_conn)
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"

    complete_job(db_conn, job_id, result={"ok": True})
    job = get_job(db_conn, job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["result"] == '{"ok": true}'


def test_job_failed_keeps_error(db_conn):
    job_id = create_job(db_conn, type="ingest", payload={"x": 1})
    complete_job(db_conn, job_id, error="boom")
    job = get_job(db_conn, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "boom"


def test_requeue_stale_jobs(db_conn):
    create_job(db_conn, type="ingest", payload={"a": 1})
    create_job(db_conn, type="ingest", payload={"b": 2})
    claim_job(db_conn)  # leave one running

    ids = requeue_stale_jobs(db_conn)
    assert len(ids) == 1

    rows = db_conn.execute("SELECT id, status FROM jobs").fetchall()
    assert all(r["status"] == "queued" for r in rows)


def test_claim_job_empty(db_conn):
    assert claim_job(db_conn) is None


# ---------------------------------------------------------------------------
# broadcaster
# ---------------------------------------------------------------------------

def test_broadcaster_publish_subscribe():
    async def _run():
        q = await broadcaster.subscribe(999)
        await broadcaster.publish(999, {"kind": "progress", "progress": 50})
        event = await asyncio.wait_for(q.get(), timeout=1)
        assert event["kind"] == "progress"
        await broadcaster.unsubscribe(999)

    asyncio.run(_run())
