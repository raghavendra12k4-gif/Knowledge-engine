import time

import pymupdf

from app.core.config import settings


def _wait_done(client, doc_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = client.get(f"/api/documents/{doc_id}").json()
        if d["status"] not in ("queued", "processing"):
            return d
        time.sleep(0.05)
    return client.get(f"/api/documents/{doc_id}").json()


def _pdf_bytes(text="Hello PDF world", pages=1):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72 + i * 12), f"{text} page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _pptx_bytes(slides):
    import io
    import zipfile

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>'''

    presentation = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId2"/>
    <p:sldId id="257" r:id="rId3"/>
  </p:sldIdLst>
</p:presentation>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>'''

    slide_xml = lambda text: f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:spTree>
    <p:sp>
      <p:txBody>
        <a:bodyPr/>
        <a:lstStyle/>
        <a:p><a:r><a:t>{text}</a:t></a:r></a:p>
      </p:txBody>
    </p:sp>
  </p:spTree>
</p:sld>'''

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("ppt/presentation.xml", presentation)
        zf.writestr("ppt/_rels/presentation.xml.rels", rels)
        for idx, text in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml(text))
    return buf.getvalue()


def test_pdf_upload_creates_chunks_and_page_images(client):
    pdf = _pdf_bytes("This is extractable PDF text " * 12, pages=2)
    r = client.post(
        "/api/documents/upload",
        files={"file": ("sample.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    doc_id = r.json()["document"]["id"]
    doc = _wait_done(client, doc_id)
    assert doc["status"] == "ready", doc

    chunks = client.get(f"/api/documents/{doc_id}/chunks").json()["chunks"]
    assert chunks
    assert all(c["text"].strip() for c in chunks)
    assert all(c["locator"]["type"] == "pdf" for c in chunks)

    pages = client.get(f"/api/documents/{doc_id}/pages").json()["pages"]
    assert len(pages) == 2
    img = client.get(f"/api/documents/{doc_id}/pages/1/image")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/png")
    assert (settings.pages_dir / str(doc_id)).exists()


def test_pptx_upload_creates_slide_chunks(client):
    pptx = _pptx_bytes(["Slide 1 content", "Slide 2 content"])
    r = client.post(
        "/api/documents/upload",
        files={"file": ("sample.pptx", pptx, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert r.status_code == 200
    doc_id = r.json()["document"]["id"]
    doc = _wait_done(client, doc_id)
    assert doc["status"] == "ready", doc

    chunks = client.get(f"/api/documents/{doc_id}/chunks").json()["chunks"]
    assert [c["locator"]["type"] for c in chunks] == ["pptx", "pptx"]
    assert [c["locator"]["slide_start"] for c in chunks] == [1, 2]
    assert all(c["text"] for c in chunks)


def test_scanned_page_flags_needs_ocr(client):
    # PDF with a raster image and almost no text.
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(20, 20, 180, 180), pixmap=pix)
    pdf = doc.tobytes()
    doc.close()

    r = client.post("/api/documents/upload", files={"file": ("scan.pdf", pdf, "application/pdf")})
    doc_id = r.json()["document"]["id"]
    done = _wait_done(client, doc_id)
    assert done["status"] in ("needs_review", "ready")
    pages = client.get(f"/api/documents/{doc_id}/pages").json()["pages"]
    assert len(pages) == 1


def test_reprocess_does_not_duplicate_chunks(client):
    r = client.post(
        "/api/documents/upload",
        files={"file": ("dupcheck.md", b"# Title\n\n" + b"content " * 80, "text/markdown")},
    )
    doc_id = r.json()["document"]["id"]
    assert _wait_done(client, doc_id)["status"] == "ready"
    c1 = client.get(f"/api/documents/{doc_id}/chunks").json()["chunks"]

    retry = client.post(f"/api/documents/{doc_id}/reprocess")
    assert retry.status_code == 200
    _wait_done(client, doc_id)
    c2 = client.get(f"/api/documents/{doc_id}/chunks").json()["chunks"]
    assert len(c2) == len(c1)
    assert [c["ordinal"] for c in c2] == list(range(1, len(c2) + 1))


def test_delete_removes_chunks_search_rows_and_page_images(client):
    pdf = _pdf_bytes("Delete me " * 40, pages=1)
    r = client.post("/api/documents/upload", files={"file": ("gone.pdf", pdf, "application/pdf")})
    doc_id = r.json()["document"]["id"]
    assert _wait_done(client, doc_id)["status"] == "ready"
    assert client.get(f"/api/documents/{doc_id}/chunks").json()["chunks"]
    page_dir = settings.pages_dir / str(doc_id)
    assert page_dir.exists()

    d = client.delete(f"/api/documents/{doc_id}")
    assert d.status_code == 200
    assert not page_dir.exists()
