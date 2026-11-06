import shutil
import sqlite3
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.config import settings
from app.core.db import get_db
from app.core.jobs import create_job
from app.services.documents import (
    DuplicateDocumentError,
    count_documents,
    create_document,
    delete_document,
    get_document,
    get_document_by_hash,
    list_documents,
    set_status,
)
from app.services.ingest import IngestError, classify_kind, copy_original, hash_file

router = APIRouter()


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
):
    """Upload a file. Validates content by magic bytes, hashes it,
    detects duplicates, stores the original, creates a document row
    and an ingest job."""
    temp_name = f".upload_{uuid.uuid4().hex}_{file.filename or 'upload'}"
    temp_path = settings.inbox_dir / temp_name

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Validate content by magic bytes before hashing.
        from app.services.ingest import detect_kind_by_magic

        detected = detect_kind_by_magic(temp_path)
        if detected is None:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"unrecognized or corrupt file type: {file.filename}",
            )

        file_hash = hash_file(temp_path)

        existing = get_document_by_hash(db, file_hash)
        if existing is not None:
            temp_path.unlink(missing_ok=True)
            return {"duplicate": True, "document": existing}

        kind = classify_kind(temp_path)
        original_path = copy_original(temp_path, file_hash)
        temp_path.unlink(missing_ok=True)

        doc = create_document(
            db,
            kind=kind,
            title=file.filename or temp_path.name,
            path=str(original_path),
            file_hash=file_hash,
            mime=file.content_type,
            size_bytes=original_path.stat().st_size,
        )

        job_id = create_job(
            db,
            type="ingest",
            payload={"document_id": doc["id"], "file_hash": file_hash},
        )

        return {"document": doc, "job_id": job_id, "duplicate": False}

    except DuplicateDocumentError as exc:
        temp_path.unlink(missing_ok=True)
        return {"duplicate": True, "document": exc.existing}
    except IngestError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
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

    job_id = create_job(
        db,
        type="ingest",
        payload={"document_id": doc_id, "file_hash": doc["file_hash"]},
    )
    set_status(db, doc_id, "queued")

    return {"job_id": job_id, "document_id": doc_id}
