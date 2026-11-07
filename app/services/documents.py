import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KINDS = ("note", "pdf", "docx", "pptx", "image", "web", "report", "text")
STATUSES = ("queued", "processing", "ready", "needs_review", "failed")


class DuplicateDocumentError(Exception):
    def __init__(self, existing: dict):
        super().__init__("duplicate document")
        self.existing = existing


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "tags" in d and d["tags"] is not None:
        d["tags"] = json.loads(d["tags"])
    return d


def create_document(
    conn, *, kind, title, path, file_hash=None, mime=None, size_bytes=None,
    source_url=None, language=None, tags=None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"invalid kind: {kind}")
    if tags is None:
        tags = []
    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO documents (kind, title, path, source_url, file_hash, mime,
                                     size_bytes, status, error, ocr_quality, language,
                                     tags, created_at, updated_at, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?, ?, ?, NULL)""",
            (kind, title, path, source_url, file_hash, mime, size_bytes, language,
             json.dumps(tags), now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        existing = get_document_by_hash(conn, file_hash) if file_hash else None
        if existing is None:
            raise
        raise DuplicateDocumentError(existing) from None
    doc = get_document(conn, cur.lastrowid)
    if doc is None:
        raise RuntimeError("document row missing right after insert")
    return doc


def get_document(conn, doc_id) -> dict | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return None if row is None else _row_to_dict(row)


def get_document_by_hash(conn, file_hash) -> dict | None:
    row = conn.execute("SELECT * FROM documents WHERE file_hash = ?", (file_hash,)).fetchone()
    return None if row is None else _row_to_dict(row)


def _filters(status, kind, table_alias: str | None = None):
    sql = " WHERE 1=1"
    params = []
    if status is not None:
        sql += f" AND {table_alias + '.' if table_alias else ''}status = ?"
        params.append(status)
    if kind is not None:
        sql += f" AND {table_alias + '.' if table_alias else ''}kind = ?"
        params.append(kind)
    return sql, params


def list_documents(conn, *, status=None, kind=None, limit=100, offset=0) -> list[dict]:
    where, params = _filters(status, kind, table_alias="d")
    sql = """SELECT d.*, j.id AS job_id, j.progress AS job_progress
             FROM documents AS d
             LEFT JOIN jobs AS j ON j.id = (
                 SELECT MAX(candidate.id)
                 FROM jobs AS candidate
                 WHERE candidate.type = 'ingest'
                   AND json_extract(candidate.payload, '$.document_id') = d.id
             )""" + where + " ORDER BY d.id DESC LIMIT ? OFFSET ?"
    rows = conn.execute(sql, [*params, limit, offset]).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_documents(conn, *, status=None, kind=None) -> int:
    where, params = _filters(status, kind)
    return conn.execute("SELECT COUNT(*) FROM documents" + where, params).fetchone()[0]


def set_status(conn, doc_id, status, *, error=None) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    now = _now()
    if status == "failed":
        conn.execute(
            "UPDATE documents SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, now, doc_id),
        )
    elif status in ("ready", "needs_review"):
        conn.execute(
            "UPDATE documents SET status = ?, error = NULL, updated_at = ?, "
            "ingested_at = ? WHERE id = ?",
            (status, now, now, doc_id),
        )
    else:
        conn.execute(
            "UPDATE documents SET status = ?, error = NULL, updated_at = ? WHERE id = ?",
            (status, now, doc_id),
        )
    conn.commit()
    return get_document(conn, doc_id)


def update_document(
    conn, doc_id, *, title=None, tags=None, language=None, ocr_quality=None,
) -> dict | None:
    row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    fields = []
    params = []
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if tags is not None:
        fields.append("tags = ?")
        params.append(json.dumps(tags))
    if language is not None:
        fields.append("language = ?")
        params.append(language)
    if ocr_quality is not None:
        fields.append("ocr_quality = ?")
        params.append(ocr_quality)
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(doc_id)
    conn.execute(f"UPDATE documents SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    return get_document(conn, doc_id)


def delete_document(conn, doc_id) -> bool:
    doc = get_document(conn, doc_id)
    if doc is None:
        return False
    # Remove derived A2 data explicitly (FTS rows are not always FK-cascaded).
    try:
        conn.execute("DELETE FROM chunks_fts WHERE document_id = ?", (doc_id,))
    except sqlite3.Error:
        logger.exception("Failed to delete FTS rows for document %s", doc_id)
    try:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
    except sqlite3.Error:
        logger.exception("Failed to delete chunk rows for document %s", doc_id)
    try:
        conn.execute("DELETE FROM pages WHERE document_id = ?", (doc_id,))
    except sqlite3.Error:
        logger.exception("Failed to delete page rows for document %s", doc_id)
    conn.execute("DELETE FROM jobs WHERE json_extract(payload, '$.document_id') = ?", (doc_id,))
    cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    if cur.rowcount <= 0:
        return False
    try:
        import shutil

        from app.core.config import settings

        pages_dir = settings.pages_dir / str(doc_id)
        if pages_dir.exists():
            shutil.rmtree(pages_dir, ignore_errors=True)
    except OSError:
        logger.exception("Could not delete page images for document %s", doc_id)
    remaining = conn.execute(
        "SELECT 1 FROM documents WHERE file_hash = ? LIMIT 1", (doc.get("file_hash"),)
    ).fetchone()
    if remaining is None and doc.get("path"):
        try:
            Path(doc["path"]).unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not delete original file for document %s", doc_id)
    return True
