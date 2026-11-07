from app.services.chunk import chunk_docx_paragraphs, chunk_text_document


def test_chunker_respects_headings_and_paths():
    text = """# Chapter 2
Intro text.

## Sorting
Sorting intro.

## Searching
Searching intro.
"""
    chunks = chunk_text_document(text, document_id=1, kind="text")
    assert chunks
    assert not any(
        c.heading_path and "Sorting" in c.heading_path and "Searching" in c.heading_path
        for c in chunks
    )
    assert any(c.heading_path == "Chapter 2 > Sorting" for c in chunks)


def test_chunker_never_splits_code_block():
    code = "\n".join(f"line {i}" for i in range(120))
    text = f"""# Code

```python
{code}
```

After.
"""
    chunks = chunk_text_document(text, document_id=1, kind="text")
    code_chunks = [c for c in chunks if "```python" in c.text]
    assert len(code_chunks) == 1
    assert "line 119" in code_chunks[0].text
    assert code_chunks[0].text.rstrip().endswith("```")


def test_chunker_never_splits_table():
    rows = "\n".join(f"| {i} | value {i} |" for i in range(80))
    text = f"""# Table

| k | v |
|---|---|
{rows}

Tail.
"""
    chunks = chunk_text_document(text, document_id=1, kind="text")
    table_chunks = [c for c in chunks if "| k | v |" in c.text]
    assert len(table_chunks) == 1
    assert "| 79 | value 79 |" in table_chunks[0].text


def test_chunker_overlap_and_sentence_split():
    para = " ".join([f"Sentence {i} is complete." for i in range(220)])
    text = f"# Big\n\n{para}\n"
    chunks = chunk_text_document(text, document_id=1, kind="text")
    assert len(chunks) >= 2
    assert all(c.token_count <= 500 for c in chunks)
    if len(chunks) >= 3:
        assert any(word in chunks[2].text for word in chunks[1].text.split()[-20:])


def test_docx_paragraph_chunking_locator():
    chunks = chunk_docx_paragraphs(["Para 1", "", "Para 2 " * 60, "Para 3"], document_id=7)
    assert chunks
    assert all(c.locator["type"] == "docx" for c in chunks)
    assert all(c.locator["document_id"] == 7 for c in chunks)
    assert chunks[0].locator["paragraph_start"] >= 1


def test_chunker_text_never_straddles_headings():
    text = """# Alpha
Alpha content.

# Beta
Beta content.
"""
    chunks = chunk_text_document(text, document_id=1, kind="text")
    for c in chunks:
        has_alpha = "Alpha content." in c.text
