import io
import time

import pymupdf
from docx import Document
from PIL import Image


def _mixed_files():
    pdf = pymupdf.open()
    pdf.new_page()
    pdf_bytes = pdf.tobytes()
    pdf.close()

    docx = Document()
    docx.add_paragraph("A1 test")
    docx_buffer = io.BytesIO()
    docx.save(docx_buffer)

    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image_buffer, format="PNG")

    return [
        ("notes.md", b"# A1\n", "text/markdown"),
        ("document.pdf", pdf_bytes, "application/pdf"),
        (
            "document.docx",
            docx_buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("image.png", image_buffer.getvalue(), "image/png"),
        ("page.html", b"<!doctype html><html><body>A1</body></html>", "text/html"),
    ]


def _wait_for_status(client, doc_ids, status, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        docs = [client.get(f"/api/documents/{doc_id}").json() for doc_id in doc_ids]
        if all(doc["status"] == status for doc in docs):
            return docs
        time.sleep(0.05)
    return [client.get(f"/api/documents/{doc_id}").json() for doc_id in doc_ids]


def test_upload_mixed_files_and_duplicate(client):
    doc_ids = []
    for filename, content, mime in _mixed_files():
        response = client.post(
            "/api/documents/upload",
            files={"file": (filename, content, mime)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["duplicate"] is False
        assert body["job_id"] > 0
        doc_ids.append(body["document"]["id"])

    docs = _wait_for_status(client, doc_ids, "ready")
    assert all(doc["status"] == "ready" for doc in docs)

    duplicate = client.post(
        "/api/documents/upload",
        files={"file": ("copy.md", b"# A1\n", "text/markdown")},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["document"]["id"] == doc_ids[0]

    listed = client.get("/api/documents").json()["documents"]
    assert all(doc["job_id"] for doc in listed)
    assert all(doc["job_progress"] == 100 for doc in listed)


def test_corrupt_file_becomes_failed_document_without_stopping_worker(client):
    response = client.post(
        "/api/documents/upload",
        files={"file": ("broken.pdf", b"%PDF-1.4\nnot a PDF", "application/pdf")},
    )
    assert response.status_code == 200
    document_id = response.json()["document"]["id"]

    docs = _wait_for_status(client, [document_id], "failed")
    assert docs[0]["error"]

    next_upload = client.post(
        "/api/documents/upload",
        files={"file": ("healthy.md", b"healthy", "text/markdown")},
    )
    assert next_upload.status_code == 200
    ready = _wait_for_status(client, [next_upload.json()["document"]["id"]], "ready")
    assert ready[0]["status"] == "ready"


def test_reprocess_creates_trackable_job(client):
    response = client.post(
        "/api/documents/upload",
        files={"file": ("retry.md", b"retryable", "text/markdown")},
    )
    document_id = response.json()["document"]["id"]
    _wait_for_status(client, [document_id], "ready")
    previous_job_id = next(
        doc["job_id"]
        for doc in client.get("/api/documents").json()["documents"]
        if doc["id"] == document_id
    )

    retry = client.post(f"/api/documents/{document_id}/reprocess")
    assert retry.status_code == 200
    new_job_id = retry.json()["job_id"]
    assert new_job_id > previous_job_id

    deadline = time.monotonic() + 5
    current = None
    while time.monotonic() < deadline:
        current = next(
            doc
            for doc in client.get("/api/documents").json()["documents"]
            if doc["id"] == document_id
        )
        if current["job_id"] == new_job_id and current["job_progress"] == 100:
            break
        time.sleep(0.05)
    assert current is not None
    assert current["job_id"] == new_job_id
    assert current["job_progress"] == 100


def test_upload_size_limit_returns_413(client, monkeypatch):
    from app.routers import documents

    monkeypatch.setattr(documents, "MAX_UPLOAD_BYTES", 8)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("large.md", b"0123456789", "text/markdown")},
    )
    assert response.status_code == 413
    assert "size limit" in response.json()["detail"]


def test_upload_filename_cannot_escape_inbox_or_originals(client, tmp_path):
    response = client.post(
        "/api/documents/upload",
        files={"file": ("..\\..\\outside.md", b"safe", "text/markdown")},
    )
    assert response.status_code == 200
    document = response.json()["document"]
    assert document["title"] == "outside.md"
    assert document["path"].startswith(str(tmp_path / "originals"))
