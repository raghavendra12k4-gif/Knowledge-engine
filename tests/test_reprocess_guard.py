import pytest

from app.core.db import connect
from app.services.documents import create_document, set_status


def _make_doc(status):
    conn = connect()
    try:
        doc = create_document(
            conn, kind="text", title=f"{status}.md", path="missing", file_hash=f"hash-{status}"
        )
        set_status(conn, doc["id"], status, error="boom" if status == "failed" else None)
        return doc["id"]
    finally:
        conn.close()


def _job_count(doc_id):
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE json_extract(payload, '$.document_id') = ?",
            (doc_id,),
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["queued", "processing"])
def test_reprocess_rejected_while_active(client, status):
    doc_id = _make_doc(status)

    response = client.post(f"/api/documents/{doc_id}/reprocess")

    assert response.status_code == 409
    assert _job_count(doc_id) == 0  # no second job was created


def test_reprocess_allowed_for_failed_document(client):
    doc_id = _make_doc("failed")

    response = client.post(f"/api/documents/{doc_id}/reprocess")

    assert response.status_code == 200
    assert response.json()["document_id"] == doc_id
    assert _job_count(doc_id) == 1


def test_reprocess_unknown_document_is_404(client):
    assert client.post("/api/documents/999999/reprocess").status_code == 404