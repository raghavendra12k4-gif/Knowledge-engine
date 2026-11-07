"""A2 extraction: PDF/native text blocks + page images, DOCX, text/markdown.

CPU/PyMuPDF work is synchronous; call via asyncio.to_thread from handlers.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

import pymupdf
from docx import Document

from app.core.config import settings
from app.core.locators import build_pptx_locator
from app.services.chunk import Block, Chunk, chunk_docx_paragraphs, chunk_text_document, pack_blocks

SCANNED_CHAR_THRESHOLD = 30
PDF_RENDER_DPI = 150


@dataclass
class PageArtifact:
    page_no: int
    image_path: str | None
    width: int | None
    height: int | None
    text: str
    text_source: str = "native"
    needs_ocr: bool = False
    render_dpi: int = PDF_RENDER_DPI


@dataclass
class ExtractionResult:
    chunks: list[Chunk]
    pages: list[PageArtifact] = field(default_factory=list)
    status: str = "ready"  # ready | needs_review
    error: str | None = None
    scanned_pages: list[int] = field(default_factory=list)


def _pdf_page_blocks(page: pymupdf.Page) -> list[Block]:
    blocks: list[Block] = []
    page_no = page.number + 1
    raw = page.get_text("dict")
    for b in raw.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines = b.get("lines", [])
        text_lines: list[str] = []
        for line in lines:
            spans = line.get("spans", [])
            text_lines.append("".join(s.get("text", "") for s in spans).rstrip())
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        bbox = b.get("bbox")
        blocks.append(
            Block(
                text=text,
                heading_path=_pdf_heading_path(page, b, lines),
                locator={
                    "type": "pdf",
                    "page": page_no,
                    "bbox": [float(v) * PDF_RENDER_DPI / 72.0 for v in bbox] if bbox else None,
                },
                page_start=page_no,
                page_end=page_no,
                kind="paragraph",
            )
        )
    return blocks


def _pdf_heading_path(page: pymupdf.Page, block: dict, lines: list[dict]) -> str:
    """Return a heading path for this block.

    Strategy (A2):
    1. If the document has an outline (TOC), find entries on this page and pick the
       entry whose page target is this page and whose y-position is closest to the
       block's top edge. We use the entry's title directly rather than reconstructing
       the full path, which is a reasonable A2 approximation.
    2. Otherwise, use a font-size heuristic: a short line whose max font size is
       noticeably larger than the page's median font size, or which is bold.
    """
    try:
        toc = page.parent.get_toc(simple=True) if page.parent else []
    except Exception:
        toc = []
    page_no = page.number + 1
    bbox = block.get("bbox") or [0, 0, 0, 0]
    top = float(bbox[1])

    candidates = [item for item in toc if len(item) >= 3 and int(item[2]) == page_no]
    if candidates:
        # Prefer entries whose title text actually appears in this block's text.
        block_text = " ".join(
            "".join(s.get("text", "") for s in line.get("spans", [])) for line in lines
        ).strip()
        title_matches = [
            item for item in candidates
            if str(item[1]).strip() and str(item[1]).strip() in block_text
        ]
        pool = title_matches or candidates
        # Pick the deepest (last in document order) matching entry rather than
        # a constant per-page choice.
        best = pool[-1]
        title = str(best[1]).strip()
        if title:
            return re.sub(r"\s+", " ", title)

    spans = [s for line in lines for s in line.get("spans", [])]
    if not spans:
        return ""
    sizes = [float(s.get("size", 0)) for s in spans if s.get("size")]
    if not sizes:
        return ""
    parts = [
        "".join(s.get("text", "") for s in line.get("spans", [])) for line in lines
    ]
    text = " ".join(parts).strip()
    if not text or len(text) > 140 or "\n" in text:
        return ""
    max_size = max(sizes)
    # Compare against the page's median font size, not just this block's, so a
    # heading consisting of a single span can still be detected.
    page_spans = []
    try:
        for b in page.get_text("dict").get("blocks", []):
            for ln in b.get("lines", []):
                page_spans.extend(ln.get("spans", []))
    except Exception:
        page_spans = []
    page_sizes = [float(s.get("size", 0)) for s in page_spans if s.get("size")]
    median = sorted(page_sizes)[len(page_sizes) // 2] if page_sizes else (sorted(sizes)[len(sizes) // 2])
    flags = [int(s.get("flags", 0)) for s in spans]
    boldish = any(f & 16 for f in flags)
    if boldish or max_size >= median + 1.0:
        return re.sub(r"\s+", " ", text)
    return ""


def _is_scanned_page(page: pymupdf.Page, text: str) -> bool:
    if len((text or "").strip()) >= SCANNED_CHAR_THRESHOLD:
        return False
    # PyMuPDF image info; any raster image on a low-text page suggests scan.
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    return len(images) > 0


def extract_pdf(path: Path, *, document_id: int, progress_cb=None) -> ExtractionResult:
    pages_dir = settings.pages_dir / str(document_id)
    from app.services.chunks import remove_page_images
    remove_page_images(document_id)
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_artifacts: list[PageArtifact] = []
    all_blocks: list[Block] = []
    scanned_pages: list[int] = []
    total_text_chars = 0

    with pymupdf.open(path) as doc:
        page_count = doc.page_count or 0
        if page_count == 0:
            return ExtractionResult(
                chunks=[], pages=[], status="needs_review", error="PDF has no pages"
            )

        batch = max(1, min(10, page_count))
        for i in range(page_count):
            page = doc[i]
            page_no = i + 1
            text = page.get_text("text") or ""
            total_text_chars += len(text.strip())
            scanned = _is_scanned_page(page, text)
            if scanned:
                scanned_pages.append(page_no)

            pix = page.get_pixmap(dpi=PDF_RENDER_DPI, alpha=False)
            image_name = f"page-{page_no:04d}.png"
            image_path = pages_dir / image_name
            pix.save(str(image_path))

            page_artifacts.append(
                PageArtifact(
                    page_no=page_no,
                    image_path=str(image_path.relative_to(settings.data_dir)).replace("\\", "/"),
                    width=pix.width,
                    height=pix.height,
                    text=text,
                    text_source="ocr" if scanned else "native",
                    needs_ocr=scanned,
                    render_dpi=PDF_RENDER_DPI,
                )
            )

            if not scanned:
                blocks = _pdf_page_blocks(page)
                # Ensure locators carry document_id for later UI/API consumers.
                for b in blocks:
                    loc = dict(b.locator)
                    loc["document_id"] = document_id
                    loc.setdefault("page", page_no)
                    b.locator = loc
                    b.page_start = page_no
                    b.page_end = page_no
                all_blocks.extend(blocks)

            if progress_cb and ((i + 1) % batch == 0 or (i + 1) == page_count):
                progress_cb(i + 1, page_count)

    if total_text_chars == 0 and not all_blocks:
        return ExtractionResult(
            chunks=[], pages=page_artifacts, status="needs_review",
            scanned_pages=scanned_pages,
        )

    # Pack PDF blocks. We keep chunk locators as pdf+page+bbox (merged in packer).
    from app.services.chunk import pack_blocks
    chunks = pack_blocks(all_blocks, document_id=document_id, kind="pdf")
    status = "needs_review" if not chunks and page_artifacts else "ready"
    return ExtractionResult(
        chunks=chunks,
        pages=page_artifacts,
        status=status,
        scanned_pages=scanned_pages,
    )


def extract_text(path: Path, *, document_id: int) -> ExtractionResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return ExtractionResult(chunks=[], status="needs_review")
    chunks = chunk_text_document(text, document_id=document_id, kind="text")
    status = "needs_review" if not chunks else "ready"
    return ExtractionResult(chunks=chunks, status=status)


def extract_docx(path: Path, *, document_id: int) -> ExtractionResult:
    doc = Document(str(path))
    paragraphs = [p.text or "" for p in doc.paragraphs]
    if not any(p.strip() for p in paragraphs):
        return ExtractionResult(chunks=[], status="needs_review")
    chunks = chunk_docx_paragraphs(paragraphs, document_id=document_id)
    status = "needs_review" if not chunks else "ready"
    return ExtractionResult(chunks=chunks, status=status)


def _pptx_slide_texts(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "ppt/presentation.xml" not in names:
            return []

        rel_targets: dict[str, str] = {}
        rels_path = "ppt/_rels/presentation.xml.rels"
        if rels_path in names:
            root = ET.fromstring(zf.read(rels_path))
            for rel in root:
                if rel.tag.endswith("Relationship"):
                    rel_id = rel.attrib.get("Id")
                    target = rel.attrib.get("Target", "")
                    if rel_id is not None:
                        rel_targets[rel_id] = target

        presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
        ns = {
            "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        slide_targets: list[str] = []
        for node in presentation.findall(".//p:sldIdLst/p:sldId", ns):
            rel_id = node.attrib.get(f"{{{ns['r']}}}id")
            if rel_id and rel_id in rel_targets:
                slide_targets.append(rel_targets[rel_id])

        if not slide_targets:
            slide_targets = [
                name
                for name in sorted(names)
                if name.startswith("ppt/slides/") and name.endswith(".xml")
            ]

        text_by_slide: list[str] = []
        for target in slide_targets:
            slide_name = target if target.startswith("ppt/") else f"ppt/{target.lstrip('/')}"
            if slide_name not in names:
                continue
            root = ET.fromstring(zf.read(slide_name))
            parts = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
            text = "\n".join(parts).strip()
            if text:
                text_by_slide.append(text)
        return text_by_slide


def extract_pptx(path: Path, *, document_id: int) -> ExtractionResult:
    slides = _pptx_slide_texts(path)
    if not slides:
        return ExtractionResult(chunks=[], status="needs_review")

    blocks: list[Block] = []
    for idx, slide_text in enumerate(slides, start=1):
        text = (slide_text or "").strip()
        if not text:
            continue
        blocks.append(
            Block(
                text=text,
                heading_path="",
                locator=build_pptx_locator(
                    document_id=document_id,
                    slide_start=idx,
                    slide_end=idx,
                ),
                page_start=idx,
                page_end=idx,
                kind="paragraph",
            )
        )

    chunks = pack_blocks(blocks, document_id=document_id, kind="pptx")
    status = "needs_review" if not chunks else "ready"
    return ExtractionResult(chunks=chunks, status=status)


def extract_document(
    path: Path, *, document_id: int, kind: str, progress_cb=None
) -> ExtractionResult:
    if kind == "pdf":
        return extract_pdf(path, document_id=document_id, progress_cb=progress_cb)
    if kind == "docx":
        return extract_docx(path, document_id=document_id)
    if kind == "pptx":
        return extract_pptx(path, document_id=document_id)
    if kind in {"text", "note", "report"}:
        return extract_text(path, document_id=document_id)
    # image/html/web: ingested as today, no chunks in A2.
    return ExtractionResult(chunks=[], pages=[], status="ready")
