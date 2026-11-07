-- A2: pages, chunks, and keyword search for extracted content.
-- chunks_fts is managed explicitly in the same transactions as chunks (no triggers),
-- because chunk rows include document title context and FTS rows must be removed
-- reliably on reprocess/delete.

ALTER TABLE documents ADD COLUMN ocr_quality REAL;

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    image_path TEXT,
    width INTEGER,
    height INTEGER,
    text TEXT,
    text_source TEXT NOT NULL DEFAULT 'native'
        CHECK (text_source IN ('native', 'ocr', 'vision', 'manual')),
    render_dpi INTEGER NOT NULL DEFAULT 150,
    needs_ocr INTEGER NOT NULL DEFAULT 0 CHECK (needs_ocr IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(document_id, page_no)
);

CREATE INDEX IF NOT EXISTS idx_pages_document ON pages(document_id, page_no);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading_path TEXT,
    page_start INTEGER,
    page_end INTEGER,
    locator TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    flagged INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0, 1)),
    embed_model TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_flagged ON chunks(flagged) WHERE flagged = 1;

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    title,
    heading_path,
    text,
    tokenize = 'unicode61',
    prefix = '2 3'
);
