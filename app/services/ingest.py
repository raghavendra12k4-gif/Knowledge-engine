import hashlib
import json
import logging
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.db import connect
from app.core.jobs import create_job, update_progress
from app.core.progress import broadcaster
from app.services.documents import (
    DuplicateDocumentError,
    create_document,
    get_document,
    get_document_by_hash,
    set_status,
)

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """Raised when a file cannot be ingested (validation failure, etc.)."""


_EXTENSION_MAP = {
    ".md": "text",
    ".txt": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".html": "web",
    ".htm": "web",
}

# Magic byte signatures for content validation.
_MAGIC = {
    b"%PDF-": "pdf",
    b"PK\x03\x04": "docx",
    b"\x89PNG\r\n\x1a\n": "image",
    b"\xff\xd8\xff": "image",
}


def hash_file(path: Path | str) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    path = Path(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_kind(path: Path | str) -> str:
    """Map a file extension to a documents.kind value."""
    path = Path(path)
    return _EXTENSION_MAP.get(path.suffix.lower(), "text")


def detect_kind_by_magic(path: Path | str) -> str | None:
    """Detect file kind by reading magic bytes from the file header.

    Returns the detected kind string, or None if the contents are invalid
    or unrecognized.
    """
    path = Path(path)
    try:
        with open(path, "rb") as f:
            header = f.read(262)
    except OSError:
        return None

    # Check exact magic signatures first.
    for magic, kind in _MAGIC.items():
        if header.startswith(magic):
            if magic == b"PK\x03\x04":
                try:
                    with zipfile.ZipFile(path) as zf:
                        names = set(zf.namelist())
                        if (
                            "[Content_Types].xml" not in names
                            or "word/document.xml" not in names
                            or zf.testzip() is not None
                        ):
                            return None
                        ET.fromstring(zf.read("[Content_Types].xml"))
                        ET.fromstring(zf.read("word/document.xml"))
                except (ET.ParseError, OSError, KeyError, zipfile.BadZipFile):
                    return None
            elif magic == b"%PDF-":
                try:
                    with pymupdf.open(path) as document:
                        if document.page_count == 0:
                            return None
                except (pymupdf.FileDataError, RuntimeError, ValueError):
                    return None
            elif kind == "image":
                try:
                    with Image.open(path) as image:
                        image.verify()
                except (OSError, UnidentifiedImageError, ValueError):
                    return None
            return kind

    # WEBP: starts with "RIFF" and has "WEBP" at bytes 8-11.
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError, ValueError):
            return None
        return "image"

    # HTML: check for common HTML starters (case-insensitive).
    text_start = header[:64].lstrip()
    lower = text_start.lower()
    if lower.startswith((b"<!doctype", b"<html", b"<head", b"<body")):
        return "web"

    # For text extensions, accept if there are no NUL bytes (binary indicator).
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        if b"\x00" not in header:
            return "text"
        return None

    return None


def copy_original(path: Path | str, file_hash: str) -> Path:
    """Copy an uploaded file into data/originals/<hash>.<ext> if not already there.

    The copy is never modified after creation.
    """
    path = Path(path)
    dest = settings.originals_dir / f"{file_hash}{path.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def remove_from_inbox(path: Path | str) -> None:
    """Remove a file from the inbox after successful ingestion."""
    path = Path(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not remove inbox file %s", path)


def move_to_rejected(path: Path | str) -> None:
    """Move a failed file into data/inbox/_rejected/ for inspection."""
    path = Path(path)
    try:
        rejected_dir = settings.inbox_dir / "_rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        dest = rejected_dir / path.name
        counter = 1
        stem = path.stem
        suffix = path.suffix
        while dest.exists():
            dest = rejected_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        shutil.move(str(path), str(dest))
    except OSError:
        logger.exception("Could not move failed inbox file %s to rejected folder", path)


def enqueue_ingest_job(
    conn, document_id: int, file_hash: str | None, inbox_path: Path | str | None = None
) -> int:
    payload = {"document_id": document_id, "file_hash": file_hash}
    if inbox_path is not None:
        payload["inbox_path"] = str(inbox_path)
    job_id = create_job(conn, type="ingest", payload=payload)
    payload["job_id"] = job_id
    conn.execute(
        "UPDATE jobs SET payload = ? WHERE id = ?", (json.dumps(payload), job_id)
    )
    conn.commit()
    return job_id


def ingest_file(
    path: Path | str,
    conn,
    *,
    title: str | None = None,
    mime: str | None = None,
    inbox_path: Path | str | None = None,
) -> dict:
    """Preserve the original and enqueue validation/ingestion for a file.

    Invalid file contents are recorded as failed jobs by the worker, so
    watched-folder files remain visible with an error instead of disappearing.
    """
    path = Path(path)
    file_hash = hash_file(path)

    existing = get_document_by_hash(conn, file_hash)
    if existing is not None:
        raise DuplicateDocumentError(existing)

    original_path = copy_original(path, file_hash)
    doc = create_document(
        conn,
        kind=classify_kind(path),
        title=title or path.name,
        path=str(original_path),
        file_hash=file_hash,
        mime=mime,
        size_bytes=path.stat().st_size,
    )
    job_id = enqueue_ingest_job(conn, doc["id"], file_hash, inbox_path)

    return {"document": doc, "job_id": job_id}


async def handle_ingest_job(payload: dict) -> dict:
    """Job handler for ingest jobs.

    Marks the document processing, verifies the original is on disk,
    then marks it ready.  On success removes the file from the inbox.
    On failure moves it to inbox/_rejected/.
    """
    document_id = int(payload["document_id"])
    job_id = payload.get("job_id")
    inbox_path = payload.get("inbox_path")
    conn = connect()
    try:
        doc = get_document(conn, document_id)
        if doc is None:
            raise ValueError(f"document {document_id} not found")

        set_status(conn, document_id, "processing")
        if job_id is not None:
            update_progress(conn, job_id, 10.0)
        await broadcaster.publish(
            job_id if job_id is not None else document_id,
            {
                "kind": "progress",
                "document_id": document_id,
                "status": "processing",
                "progress": 10.0,
            },
        )

        original_path = Path(doc["path"])
        if not original_path.exists():
            raise FileNotFoundError(f"original not found: {original_path}")
        detected = detect_kind_by_magic(original_path)
        if detected is None:
            raise IngestError(f"unrecognized or corrupt file type: {original_path.name}")
        if classify_kind(original_path) != detected:
            raise IngestError(f"file type mismatch: {original_path.name}")

        if job_id is not None:
            update_progress(conn, job_id, 90.0)
        set_status(conn, document_id, "ready")
        await broadcaster.publish(
            job_id if job_id is not None else document_id,
            {
                "kind": "progress",
                "document_id": document_id,
                "status": "ready",
                "progress": 100.0,
            },
        )

        if inbox_path:
            remove_from_inbox(inbox_path)

        return {"document_id": document_id, "status": "ready"}
    except Exception as exc:  # noqa: BLE001 - mark failed, keep queue alive
        set_status(conn, document_id, "failed", error=str(exc))
        await broadcaster.publish(
            job_id if job_id is not None else document_id,
            {
                "kind": "failed",
                "document_id": document_id,
                "error": str(exc),
            },
        )
        if inbox_path:
            move_to_rejected(inbox_path)
        raise
    finally:
        conn.close()
