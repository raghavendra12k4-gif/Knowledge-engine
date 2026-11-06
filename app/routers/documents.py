import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.config import settings
from app.core.db import get_db
from app.services.documents import (
    DuplicateDocumentError,
    count_documents,
    delete_document,
    get_document,
    list_documents,
    set_status,
)
from app.services.ingest import enqueue_ingest_job, ingest_file

router = APIRouter()
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@router.post("/documents/upload")
def upload_document(
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
):
    """Store a bounded upload, detect duplicates, and enqueue content validation."""
    filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    filename = filename.replace("\x00", "").strip() or "upload"
    suffix = Path(filename).suffix.lower()
    if len(suffix) > 20 or not suffix[1:].isalnum():
        suffix = ""
    temp_name = f".upload_{uuid.uuid4().hex}{suffix}"
    temp_path = settings.inbox_dir / temp_name

    try:
        with open(temp_path, "wb") as buffer:
            total = 0
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="file exceeds upload size limit")
                buffer.write(chunk)

        result = ingest_file(temp_path, db, title=filename, mime=file.content_type)
        temp_path.unlink(missing_ok=True)
        return {**result, "duplicate": False}

    except DuplicateDocumentError as exc:
        temp_path.unlink(missing_ok=True)
        return {"duplicate": True, "document": exc.existing}
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@router.get("/documents")
def list_documents_route(
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
):
    """List documents with optional status/kind filters."""
    docs = list_documents(db, status=status, kind=kind, limit=limit, offset=offset)
    total = count_documents(db, status=status, kind=kind)
    return {"documents": docs, "total": total}


@router.get("/documents/{doc_id}")
def get_document_route(
    doc_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    """Get a single document by id."""
    doc = get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.delete("/documents/{doc_id}")
def delete_document_route(
    doc_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    """Delete a document."""
    doc = get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    ok = delete_document(db, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    return {"deleted": True, "id": doc_id}


@router.post("/documents/{doc_id}/reprocess")
def reprocess_document(
    doc_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    """Re-queue an ingest job for an existing document."""
    doc = get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    set_status(db, doc_id, "queued")
    job_id = enqueue_ingest_job(db, doc_id, doc["file_hash"])

    return {"job_id": job_id, "document_id": doc_id}
