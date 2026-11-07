from app.core.locators import (
    build_docx_locator,
    build_note_locator,
    build_pdf_locator,
    dumps_locator,
    loads_locator,
)


def test_pdf_locator_round_trip():
    loc = build_pdf_locator(document_id=12, page=7, bbox=[72.0, 310.0, 520.0, 402.0])
    s = dumps_locator(loc)
    assert loads_locator(s) == loc


def test_note_locator_round_trip():
    loc = build_note_locator(document_id=5, line_start=14, line_end=19)
    assert loads_locator(dumps_locator(loc)) == loc


def test_docx_locator_round_trip():
    loc = build_docx_locator(document_id=9, paragraph_start=2, paragraph_end=4)
    assert loads_locator(dumps_locator(loc)) == loc
