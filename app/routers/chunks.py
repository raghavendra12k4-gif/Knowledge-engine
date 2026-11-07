import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.db import get_db
from app.services.chunks import (
    get_chunk,
    get_page,
    list_chunks_for_document,
    list_pages_for_document,
)

router = APIRouter(tags=["chunks"])


@router.get("/documents/{document_id}/chunks")
def list_chunks(document_id: int, conn=Depends(get_db)):
    rows = list_chunks_for_document(conn, document_id)
    return {
        "chunks": [
            {
                "id": r["id"],
                "ordinal": r["ordinal"],
                "text": r["text"],
                "heading_path": r["heading_path"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "locator": r["locator"],
                "token_count": r["token_count"],
                "confidence": r["confidence"],
                "flagged": bool(r["flagged"]),
            }
            for r in rows
        ]
    }


@router.get("/documents/{document_id}/chunks/{chunk_id}")
def get_chunk_route(document_id: int, chunk_id: int, conn=Depends(get_db)):
    row = get_chunk(conn, document_id, chunk_id)
    if row is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    return {
        "id": row["id"],
        "ordinal": row["ordinal"],
        "text": row["text"],
        "heading_path": row["heading_path"],
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "locator": row["locator"],
        "token_count": row["token_count"],
        "confidence": row["confidence"],
        "flagged": bool(row["flagged"]),
    }


@router.get("/documents/{document_id}/pages")
def list_pages(document_id: int, conn=Depends(get_db)):
    rows = list_pages_for_document(conn, document_id)
    return {
        "pages": [
            {
                "id": r["id"],
                "page_no": r["page_no"],
                "image_path": r["image_path"],
                "width": r["width"],
                "height": r["height"],
                "text": r["text"],
                "text_source": r["text_source"],
                "needs_ocr": bool(r["needs_ocr"]),
                "render_dpi": r["render_dpi"],
            }
            for r in rows
        ]
    }


@router.get("/documents/{document_id}/pages/{page_no}")
def get_page_route(document_id: int, page_no: int, conn=Depends(get_db)):
    row = get_page(conn, document_id, page_no)
    if row is None:
        raise HTTPException(status_code=404, detail="page not found")
    return {
        "id": row["id"],
        "page_no": row["page_no"],
        "image_path": row["image_path"],
        "width": row["width"],
        "height": row["height"],
        "text": row["text"],
        "text_source": row["text_source"],
        "needs_ocr": bool(row["needs_ocr"]),
        "render_dpi": row["render_dpi"],
    }


@router.get("/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: int, page_no: int, conn=Depends(get_db)):
    row = get_page(conn, document_id, page_no)
    if row is None:
        raise HTTPException(status_code=404, detail="page not found")
    image_path = row.get("image_path")
    if not image_path:
        raise HTTPException(status_code=404, detail="page image not available")
    file_path = settings.data_dir / Path(image_path).as_posix().lstrip("/")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="page image not found")
    return FileResponse(file_path, media_type="image/png")