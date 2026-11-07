"""Pure structure-aware chunking for A2.

Rules:
- Split at headings/paragraph boundaries first.
- Pack up to ~250-400 tokens per chunk with ~15% overlap.
- Never split inside fenced code blocks or Markdown tables.
- Split oversized paragraphs at sentence boundaries.
- Store heading_path separately from chunk text.
- Chunks never straddle two headings or two PDF pages.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.locators import (
    build_docx_locator,
    build_note_locator,
    build_pdf_locator,
    build_pptx_locator,
)

TARGET_TOKENS = 320
MAX_TOKENS = 420
MIN_TOKENS = 40
OVERLAP_RATIO = 0.15
PDF_RENDER_DPI = 150

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:-]+\|[\s|:-]*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class Block:
    text: str
    heading_path: str
    locator: dict
    page_start: int | None = None
    page_end: int | None = None
    kind: str = "paragraph"  # paragraph|code|table|heading


@dataclass
class Chunk:
    ordinal: int
    text: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    locator: dict
    token_count: int


def estimate_tokens(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    # Simple approximation (A2 decision): word-ish + punctuation density.
    # Recomputed/replaced by embedding tokenizer in A4.
    words = len(re.findall(r"\S+", text))
    chars = len(text)
    return max(1, int(math.ceil(max(words * 1.3, chars / 4.2))))


def _pdf_bbox_to_pixels(bbox: list[float], width: int, height: int) -> list[float] | None:
    """Convert PyMuPDF page-coordinate bbox to pixel coordinates for the rendered image.

    PDF coordinates are in points (72/inch). The page image is rendered at PDF_RENDER_DPI
    (150). The scale factor is therefore render_dpi / 72.
    """
    if not bbox or len(bbox) != 4 or not width or not height:
        return None
    scale = PDF_RENDER_DPI / 72.0
    return [v * scale for v in bbox]


def _merge_bbox(boxes: list[list[float]]) -> list[float] | None:
    valid = [b for b in boxes if b and len(b) == 4]
    if not valid:
        return None
    return [
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    ]


def _join_text(parts: Iterable[str]) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip()).strip()


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    if len(parts) == 1:
        # fallback: split on newlines/sentence-ish boundaries
        parts = [p.strip() for p in re.split(r"\n+|(?<=[.!?])\s+", text) if p.strip()]
    return [p.strip() for p in parts if p.strip()] or [text]


def split_oversized_text(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    text = (text or "").strip()
    if estimate_tokens(text) <= max_tokens:
        return [text] if text else []
    sentences = _split_sentences(text)
    out: list[str] = []
    buf: list[str] = []
    for sent in sentences:
        candidate = (" ".join(buf + [sent])).strip()
        if buf and estimate_tokens(candidate) > max_tokens:
            out.append(" ".join(buf).strip())
            buf = [sent]
            # If a single sentence is still huge, hard-wrap by words.
            while buf and estimate_tokens(buf[0]) > max_tokens:
                words = buf[0].split()
                take = max(30, int(len(words) * max_tokens / max(estimate_tokens(buf[0]), 1)))
                out.append(" ".join(words[:take]).strip())
                buf = [" ".join(words[take:]).strip()] if take < len(words) else []
        else:
            buf.append(sent)
    if buf:
        out.append(" ".join(buf).strip())
    return [p for p in out if p]


def split_text_blocks(text: str, *, document_id: int) -> list[Block]:
    """Parse Markdown/plain text into atomic blocks, tracking headings/lines."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    in_fence = False
    i = 0
    n = len(lines)

    def heading_path() -> str:
        return " > ".join(h for _, h in heading_stack)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence or in_fence:
            start = i + 1
            buf = [line]
            i += 1
            if fence:
                in_fence = True
            while i < n:
                buf.append(lines[i])
                end_fence = _FENCE_RE.match(lines[i])
                i += 1
                if end_fence and in_fence:
                    in_fence = False
                    break
            block_text = "\n".join(buf).rstrip()
            blocks.append(
                Block(
                    text=block_text,
                    heading_path=heading_path(),
                    locator=build_note_locator(
                        document_id=document_id, line_start=start, line_end=i
                    ),
                    kind="code",
                )
            )
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            blocks.append(
                Block(
                    text=line.rstrip(),
                    heading_path=heading_path(),
                    locator=build_note_locator(
                        document_id=document_id, line_start=i + 1, line_end=i + 1
                    ),
                    kind="heading",
                )
            )
            i += 1
            continue

        # Markdown table: header + separator + rows (kept atomic).
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            start = i + 1
            buf = [line, lines[i + 1]]
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                buf.append(lines[i])
                i += 1
            blocks.append(
                Block(
                    text="\n".join(buf).rstrip(),
                    heading_path=heading_path(),
                    locator=build_note_locator(
                    document_id=document_id, line_start=start, line_end=i
                ),
                    kind="table",
                )
            )
            continue

        # Paragraph: contiguous non-empty, non-special lines.
        start = i + 1
        buf = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                break
            if _HEADING_RE.match(nxt) or _FENCE_RE.match(nxt):
                break
            if "|" in nxt and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
                break
            buf.append(nxt)
            i += 1
        blocks.append(
            Block(
                text="\n".join(buf).rstrip(),
                heading_path=heading_path(),
                locator=build_note_locator(
                    document_id=document_id, line_start=start, line_end=i
                ),
                kind="paragraph",
            )
        )
    return blocks


def _chunk_key(block: Block) -> str:
    # Never straddle headings: heading_path is the partition key.
    return block.heading_path or ""


def _overlap_tail(blocks: list[Block]) -> list[Block]:
    if not blocks:
        return []
    if len(blocks) == 1:
        b = blocks[0]
        sents = _split_sentences(b.text)
        if len(sents) <= 1:
            return []
        target = max(1, int(estimate_tokens(b.text) * OVERLAP_RATIO))
        picked: list[str] = []
        picked_tokens = 0
        for s in reversed(sents):
            picked.insert(0, s)
            picked_tokens += estimate_tokens(s)
            if picked_tokens >= target:
                break
        if not picked or len(picked) >= len(sents):
            return []
        tail_text = " ".join(picked).strip()
        return [Block(**{**b.__dict__, "text": tail_text})]
    total_tokens = sum(estimate_tokens(b.text) for b in blocks)
    if total_tokens <= 1:
        return []
    target = max(1, int(total_tokens * OVERLAP_RATIO))
    picked: list[Block] = []
    picked_tokens = 0
    for b in reversed(blocks):
        picked.insert(0, b)
        picked_tokens += estimate_tokens(b.text)
        if picked_tokens >= target:
            break
    if len(picked) >= len(blocks):
        return []
    return picked


def pack_blocks(blocks: list[Block], *, document_id: int, kind: str) -> list[Chunk]:
    expanded: list[Block] = []
    for b in blocks:
        if b.kind == "paragraph" and estimate_tokens(b.text) > MAX_TOKENS:
            for part in split_oversized_text(b.text):
                expanded.append(Block(**{**b.__dict__, "text": part}))
        else:
            expanded.append(b)

    def build_chunk(blocks_for_chunk: list[Block]) -> Chunk | None:
        if not blocks_for_chunk:
            return None
        text_parts = [b.text for b in blocks_for_chunk if b.kind != "heading" or len(blocks_for_chunk) == 1]
        text = _join_text(text_parts)
        if not text:
            return None
        pages = [p for b in blocks_for_chunk for p in (b.page_start, b.page_end) if p is not None]
        boxes = [b.locator.get("bbox") for b in blocks_for_chunk if isinstance(b.locator, dict)]
        heading_path = blocks_for_chunk[0].heading_path or None
        if kind == "pdf":
            page = min(pages) if pages else int(blocks_for_chunk[0].locator.get("page", 1))
            locator = build_pdf_locator(
                document_id=document_id,
                page=page,
                bbox=_merge_bbox([b for b in boxes if isinstance(b, list)]),
            )
        elif kind == "docx":
            pstarts = [
                int(b.locator["paragraph_start"]) for b in blocks_for_chunk if b.locator.get("type") == "docx"
            ]
            pends = [
                int(b.locator["paragraph_end"]) for b in blocks_for_chunk if b.locator.get("type") == "docx"
            ]
            locator = build_docx_locator(
                document_id=document_id,
                paragraph_start=min(pstarts) if pstarts else 1,
                paragraph_end=max(pends) if pends else 1,
            )
        elif kind == "pptx":
            sstarts = [
                int(b.locator["slide_start"]) for b in blocks_for_chunk if b.locator.get("type") == "pptx"
            ]
            sends = [
                int(b.locator["slide_end"]) for b in blocks_for_chunk if b.locator.get("type") == "pptx"
            ]
            locator = build_pptx_locator(
                document_id=document_id,
                slide_start=min(sstarts) if sstarts else 1,
                slide_end=max(sends) if sends else 1,
            )
        else:
            lstarts = [
                int(b.locator["line_start"]) for b in blocks_for_chunk if b.locator.get("type") == "note"
            ]
            lends = [
                int(b.locator["line_end"]) for b in blocks_for_chunk if b.locator.get("type") == "note"
            ]
            locator = build_note_locator(
                document_id=document_id,
                line_start=min(lstarts) if lstarts else 1,
                line_end=max(lends) if lends else 1,
            )
        return Chunk(
            ordinal=0,
            text=text,
            heading_path=heading_path,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            locator=locator,
            token_count=estimate_tokens(text),
        )

    current: list[Block] = []
    current_tokens = 0
    segments: list[list[Block]] = []

    def flush_current(*, with_overlap: bool = False) -> None:
        nonlocal current, current_tokens
        if not current:
            return
        if with_overlap:
            overlap = _overlap_tail(current)
            if overlap and overlap != current:
                segments.append(current)
                current = overlap
                current_tokens = sum(estimate_tokens(item.text) for item in current)
                return
        segments.append(current)
        current = []
        current_tokens = 0

    for idx, b in enumerate(expanded):
        if current and _chunk_key(b) != _chunk_key(current[0]):
            flush_current()

        if kind == "pdf" and current:
            cur_page = current[0].page_start
            if b.page_start is not None and cur_page is not None and b.page_start != cur_page:
                flush_current()

        if kind == "pptx" and current:
            cur_page = current[0].page_start
            if b.page_start is not None and cur_page is not None and b.page_start != cur_page:
                flush_current()

        btok = estimate_tokens(b.text)
        if b.kind == "heading":
            if current:
                flush_current()
            current = [b]
            current_tokens = btok
            flush_current()
            continue

        if b.kind in {"code", "table"}:
            if current:
                flush_current()
            segments.append([b])
            continue

        if current and current_tokens + btok > MAX_TOKENS:
            if current_tokens >= MIN_TOKENS:
                overlap = _overlap_tail(current)
                if overlap and overlap != current:
                    segments.append(current)
                    current = overlap
                    current_tokens = sum(estimate_tokens(item.text) for item in current)
                else:
                    segments.append(current)
                    current = []
                    current_tokens = 0
            else:
                segments.append(current)
                current = []
                current_tokens = 0

        if current and current_tokens + btok > MAX_TOKENS:
            segments.append(current)
            current = []
            current_tokens = 0

        current.append(b)
        current_tokens += btok

        if current_tokens >= TARGET_TOKENS and idx + 1 < len(expanded):
            next_block = expanded[idx + 1]
            if (
                next_block.kind == "paragraph"
                and _chunk_key(next_block) == _chunk_key(current[0])
                and estimate_tokens(next_block.text) < MIN_TOKENS
            ):
                continue
            overlap = _overlap_tail(current)
            if overlap and overlap != current:
                segments.append(current)
                current = overlap
                current_tokens = sum(estimate_tokens(item.text) for item in current)

    if current:
        segments.append(current)

    output: list[Chunk] = []
    for segment in segments:
        chunk = build_chunk(segment)
        if chunk is not None:
            output.append(chunk)

    final: list[Chunk] = []
    for chunk in [c for c in output if c.text.strip()]:
        if chunk.token_count > MAX_TOKENS and "```" not in chunk.text and "~~~" not in chunk.text and not re.search(r"\|\s*[-:]+\s*\|", chunk.text):
            for part in split_oversized_text(chunk.text, MAX_TOKENS):
                final.append(Chunk(
                    ordinal=0,
                    text=part,
                    heading_path=chunk.heading_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    locator=chunk.locator,
                    token_count=estimate_tokens(part),
                ))
        else:
            final.append(Chunk(**{**chunk.__dict__, "ordinal": 0}))

    for idx, chunk in enumerate(final, start=1):
        chunk.ordinal = idx

    return final


def chunk_text_document(text: str, *, document_id: int, kind: str = "text") -> list[Chunk]:
    blocks = split_text_blocks(text, document_id=document_id)
    return pack_blocks(blocks, document_id=document_id, kind=kind)


def chunk_docx_paragraphs(paragraphs: list[str], *, document_id: int) -> list[Chunk]:
    blocks: list[Block] = []
    for idx, para in enumerate(paragraphs, start=1):
        text = (para or "").strip()
        if not text:
            continue
        blocks.append(
            Block(
                text=text,
                heading_path="",
                locator=build_docx_locator(
                    document_id=document_id, paragraph_start=idx, paragraph_end=idx
                ),
                kind="paragraph",
            )
        )
    return pack_blocks(blocks, document_id=document_id, kind="docx")
