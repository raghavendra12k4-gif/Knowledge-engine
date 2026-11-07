"""Locator builders/validators for citable source regions.

Locators are JSON strings stored with chunks and evidence. They must always be
turnable into "open this exact spot" in the UI.

Spec types (5.3): pdf, image, note, web.
A2 also uses `note` for Markdown/text line ranges and `docx` for paragraph
ranges (a documented extension compatible with the same open/highlight model).
"""

from __future__ import annotations

import json
from typing import Any

Locator = dict[str, Any]

_TYPES = {"pdf", "image", "note", "web", "docx", "pptx"}


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def build_pdf_locator(*, document_id: int, page: int, bbox: list[float] | None) -> Locator:
    _require(page >= 1, "pdf locator page must be >= 1")
    loc: Locator = {"type": "pdf", "document_id": document_id, "page": page}
    if bbox is not None:
        _require(len(bbox) == 4, "bbox must have 4 values")
        loc["bbox"] = [float(v) for v in bbox]
    return loc


def build_note_locator(*, document_id: int, line_start: int, line_end: int) -> Locator:
    _require(line_start >= 1 and line_end >= line_start, "invalid line range")
    return {
        "type": "note",
        "document_id": document_id,
        "line_start": line_start,
        "line_end": line_end,
    }


def build_docx_locator(
    *, document_id: int, paragraph_start: int, paragraph_end: int
) -> Locator:
    _require(paragraph_start >= 1 and paragraph_end >= paragraph_start, "invalid paragraph range")
    return {
        "type": "docx",
        "document_id": document_id,
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
    }


def build_pptx_locator(*, document_id: int, slide_start: int, slide_end: int) -> Locator:
    _require(slide_start >= 1 and slide_end >= slide_start, "invalid slide range")
    return {
        "type": "pptx",
        "document_id": document_id,
        "slide_start": slide_start,
        "slide_end": slide_end,
    }


def build_image_locator(*, document_id: int, bbox: list[float] | None = None) -> Locator:
    loc: Locator = {"type": "image", "document_id": document_id}
    if bbox is not None:
        _require(len(bbox) == 4, "bbox must have 4 values")
        loc["bbox"] = [float(v) for v in bbox]
    return loc


def dumps_locator(locator: Locator) -> str:
    validate_locator(locator)
    return json.dumps(locator, separators=(",", ":"), ensure_ascii=False)


def loads_locator(value: str | Locator) -> Locator:
    loc = json.loads(value) if isinstance(value, str) else dict(value)
    validate_locator(loc)
    return loc


def validate_locator(locator: Locator) -> None:
    _require(isinstance(locator, dict), "locator must be an object")
    kind = locator.get("type")
    _require(kind in _TYPES, f"invalid locator type: {kind}")
    if kind == "pdf":
        _require(int(locator.get("document_id", 0)) > 0, "document_id required")
        _require(int(locator.get("page", 0)) >= 1, "page must be >= 1")
        if "bbox" in locator and locator["bbox"] is not None:
            _require(len(locator["bbox"]) == 4, "bbox must have 4 values")
    elif kind == "note":
        _require(int(locator.get("document_id", 0)) > 0, "document_id required")
        ls = int(locator.get("line_start", 0))
        le = int(locator.get("line_end", 0))
        _require(ls >= 1 and le >= ls, "invalid line range")
    elif kind == "docx":
        _require(int(locator.get("document_id", 0)) > 0, "document_id required")
        ps = int(locator.get("paragraph_start", 0))
        pe = int(locator.get("paragraph_end", 0))
        _require(ps >= 1 and pe >= ps, "invalid paragraph range")
    elif kind == "pptx":
        _require(int(locator.get("document_id", 0)) > 0, "document_id required")
        ss = int(locator.get("slide_start", 0))
        se = int(locator.get("slide_end", 0))
        _require(ss >= 1 and se >= ss, "invalid slide range")
    elif kind == "image":
        _require(int(locator.get("document_id", 0)) > 0, "document_id required")
    elif kind == "web":
        for key in ("url", "char_start", "char_end"):
            _require(key in locator, f"web locator missing {key}")
        _require(int(locator["char_end"]) >= int(locator["char_start"]), "invalid char range")
