import pytest

from app.core.db import connect, run_migrations
from app.services.documents import (
    DuplicateDocumentError,
    count_documents,
    create_document,
    delete_document,
    get_document,
    get_document_by_hash,
    list_documents,
    set_status,
    update_document,
)


def must(value):
    assert value is not None, "expected a document but got None"
    return value


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    run_migrations(c)
    yield c
    c.close()


def test_migration_tables(conn):
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "jobs", "audit_log", "pages", "chunks", "chunks_fts"} <= tables


def test_create_document(conn):
    d = create_document(conn, kind="note", title="t", path="originals/x")
    assert d["status"] == "queued"
    assert d["tags"] == []
    assert d["created_at"]


def test_duplicate_hash(conn):
    d1 = create_document(conn, kind="note", title="t", path="p", file_hash="abc")
    with pytest.raises(DuplicateDocumentError) as ei:
        create_document(conn, kind="note", title="t2", path="p2", file_hash="abc")
    assert ei.value.existing["id"] == d1["id"]
    assert count_documents(conn) == 1


def test_null_hash_no_duplicate(conn):
    create_document(conn, kind="note", title="a", path="p", file_hash=None)
    create_document(conn, kind="note", title="b", path="p2", file_hash=None)
    assert count_documents(conn) == 2


def test_invalid_kind(conn):
    with pytest.raises(ValueError):
        create_document(conn, kind="nope", title="t", path="p")


def test_get(conn):
    d = create_document(conn, kind="note", title="t", path="p")
    assert must(get_document(conn, d["id"]))["id"] == d["id"]
    assert get_document(conn, 9999) is None
    d2 = create_document(conn, kind="note", title="t2", path="p2", file_hash="h")
    assert must(get_document_by_hash(conn, "h"))["id"] == d2["id"]
    assert get_document_by_hash(conn, "nope") is None


def test_list_and_count(conn):
    for i in range(5):
        create_document(conn, kind="note", title=f"t{i}", path=f"p{i}")
    docs = list_documents(conn)
    set_status(conn, docs[0]["id"], "ready")
    assert len(list_documents(conn, status="ready")) == 1
    assert count_documents(conn, status="ready") == 1
    assert count_documents(conn) == 5
    ids = [d["id"] for d in list_documents(conn)]
    assert ids == sorted(ids, reverse=True)
    assert len(list_documents(conn, limit=2)) == 2
    assert len(list_documents(conn, limit=2, offset=2)) == 2


def test_set_status(conn):
    d = create_document(conn, kind="note", title="t", path="p")
    failed = must(set_status(conn, d["id"], "failed", error="boom"))
    assert failed["error"] == "boom"
    ready = must(set_status(conn, d["id"], "ready"))
    assert ready["error"] is None
    assert ready["ingested_at"]


def test_set_status_invalid_and_missing(conn):
    d = create_document(conn, kind="note", title="t", path="p")
    with pytest.raises(ValueError):
        set_status(conn, d["id"], "bogus")
    assert set_status(conn, 9999, "ready") is None


def test_update_document(conn):
    d = create_document(conn, kind="note", title="t", path="p")
    u = must(update_document(conn, d["id"], title="new", tags=["a", "b"]))
    assert u["title"] == "new"
    assert u["tags"] == ["a", "b"]
    assert u["language"] is None
    assert must(get_document(conn, d["id"]))["tags"] == ["a", "b"]
    assert update_document(conn, 9999, title="x") is None


def test_delete_document(conn):
    d = create_document(conn, kind="note", title="t", path="p")
    assert delete_document(conn, d["id"]) is True
    assert delete_document(conn, d["id"]) is False
