"""Persistence helpers for A2 pages/chunks/chunks_fts.

All derived rows for a document are wiped and rebuilt in one transaction.
FTS rows are managed explicitly (migration 0003 creates a standalone FTS table).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime

from app.core.config import settings
from app.core.locators import dumps_locator


def _now() -> str:
    return datetime.now(UTC).isoformat()


def wipe_document_derived(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))


def remove_page_images(document_id: int) -> None:
    path = settings.pages_dir / str(document_id)
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def store_extraction(conn: sqlite3.Connection, *, document_id: int, title: str, result) -> None:
    now = _now()
    wipe_document_derived(conn, document_id)
    for page in result.pages:
        conn.execute(
            """INSERT INTO pages
               (document_id, page_no, image_path, width, height,
                text, text_source, created_at, render_dpi, needs_ocr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document_id,
                page.page_no,
                page.image_path,
                page.width,
                page.height,
                page.text,
                page.text_source,
                now,
                getattr(page, "render_dpi", 150),
                int(bool(getattr(page, "needs_ocr", False))),
            ),
        )
    for chunk in result.chunks:
        locator_json = dumps_locator(chunk.locator)
        cur = conn.execute(
            """INSERT INTO chunks
               (document_id, ordinal, text, heading_path, page_start, page_end, locator,
                token_count, confidence, flagged, embed_model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, ?)""",
            (
                document_id,
                chunk.ordinal,
                chunk.text,
                chunk.heading_path,
                chunk.page_start,
                chunk.page_end,
                locator_json,
                chunk.token_count,
                now,
            ),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            """INSERT INTO chunks_fts (chunk_id, document_id, title, heading_path, text)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk_id, document_id, title, chunk.heading_path or "", chunk.text),
        )
    conn.commit()


def list_chunks_for_document(conn: sqlite3.Connection, document_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, document_id, ordinal, text, heading_path, page_start, page_end,
                  locator, token_count, confidence, flagged, embed_model, created_at
           FROM chunks WHERE document_id = ? ORDER BY ordinal""",
        (document_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            raw = d["locator"]
            d["locator"] = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        out.append(d)
    return out


def get_chunk(conn: sqlite3.Connection, document_id: int, chunk_id: int) -> dict | None:
    row = conn.execute(
        """SELECT id, document_id, ordinal, text, heading_path, page_start, page_end,
                  locator, token_count, confidence, flagged, embed_model, created_at
           FROM chunks WHERE document_id = ? AND id = ?""",
        (document_id, chunk_id),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        raw = d["locator"]
        d["locator"] = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    return d


def list_pages_for_document(conn: sqlite3.Connection, document_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, document_id, page_no, image_path, width, height, text, text_source,
                  created_at, render_dpi, needs_ocr
           FROM pages WHERE document_id = ? ORDER BY page_no""",
        (document_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_page(conn: sqlite3.Connection, document_id: int, page_no: int) -> dict | None:
    row = conn.execute(
        """SELECT id, document_id, page_no, image_path, width, height, text, text_source,
                  created_at, render_dpi, needs_ocr
           FROM pages WHERE document_id = ? AND page_no = ?""",
        (document_id, page_no),
    ).fetchone()
    return None if row is None else dict(row)
