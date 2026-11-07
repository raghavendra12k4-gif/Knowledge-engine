import {
  getDocument,
  getDocumentChunks,
  getDocumentPages,
  pageImageUrl,
} from "../api.js";

export function mountViewer(container, docId) {
  container.innerHTML = `
    <div class="viewer">
      <div class="viewer-header">
        <a href="#/library" class="btn btn-link">← Back to Library</a>
        <h2 id="viewer-title">Loading…</h2>
        <p id="viewer-meta" class="viewer-meta"></p>
        <p id="viewer-error" class="library-error" role="alert" hidden></p>
      </div>
      <div class="viewer-body">
        <section class="viewer-pages" id="viewer-pages" aria-label="Page images"></section>
        <section class="viewer-chunks" aria-label="Chunks">
          <h3>Chunks</h3>
          <div id="viewer-chunks-list" class="viewer-chunks-list"></div>
        </section>
      </div>
    </div>
  `;

  const titleEl = container.querySelector("#viewer-title");
  const metaEl = container.querySelector("#viewer-meta");
  const errorEl = container.querySelector("#viewer-error");
  const pagesEl = container.querySelector("#viewer-pages");
  const chunksEl = container.querySelector("#viewer-chunks-list");
  let activeChunkId = null;

  function showError(message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  }

  function drawHighlight(pageWrap, bbox, width, height) {
    if (!bbox || bbox.length !== 4 || !width || !height) return;
    const img = pageWrap.querySelector("img");
    if (!img) return;
    const [x0, y0, x1, y1] = bbox.map(Number);
    const scaleX = img.clientWidth / width;
    const scaleY = img.clientHeight / height;
    const hl = document.createElement("div");
    hl.className = "viewer-highlight";
    hl.style.left = `${x0 * scaleX}px`;
    hl.style.top = `${y0 * scaleY}px`;
    hl.style.width = `${Math.max(2, (x1 - x0) * scaleX)}px`;
    hl.style.height = `${Math.max(2, (y1 - y0) * scaleY)}px`;
    pageWrap.appendChild(hl);
  }

  function clearHighlights() {
    container.querySelectorAll(".viewer-highlight").forEach((el) => el.remove());
  }

  function renderPages(doc, pages, targetPage, bbox) {
    pagesEl.innerHTML = "";
    if (!pages.length) {
      const msg = document.createElement("p");
      msg.className = "viewer-empty";
      msg.textContent =
        doc.kind === "pdf"
          ? "No rendered pages available."
          : "No page images for this file type.";
      pagesEl.appendChild(msg);
      return;
    }
    for (const page of pages) {
      const wrap = document.createElement("div");
      wrap.className = "viewer-page";
      wrap.id = `page-${page.page_no}`;
      wrap.dataset.page = String(page.page_no);
      const label = document.createElement("div");
      label.className = "viewer-page-label";
      label.textContent = `Page ${page.page_no}`;
      const imgWrap = document.createElement("div");
      imgWrap.className = "viewer-page-image";
      const img = document.createElement("img");
      img.alt = `Page ${page.page_no}`;
      img.loading = "lazy";
      img.src = pageImageUrl(docId, page.page_no);
      imgWrap.appendChild(img);
      wrap.append(label, imgWrap);
      pagesEl.appendChild(wrap);
      if (targetPage === page.page_no && bbox) {
        img.addEventListener("load", () =>
          drawHighlight(imgWrap, bbox, page.width, page.height)
        );
        if (img.complete) drawHighlight(imgWrap, bbox, page.width, page.height);
      }
    }
  }

  function selectChunk(chunk, pages) {
    activeChunkId = chunk.id;
    container.querySelectorAll(".chunk-card").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.chunkId) === chunk.id);
    });
    clearHighlights();
    const pageNo = chunk.page_start || chunk.locator?.page;
    const bbox = chunk.locator?.bbox;
    if (pageNo) {
      const target = container.querySelector(`#page-${pageNo}`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      if (bbox) {
        const page = pages.find((p) => p.page_no === pageNo);
        if (page) {
          const wrap = target?.querySelector(".viewer-page-image");
          if (wrap) drawHighlight(wrap, bbox, page.width, page.height);
        }
      }
    }
  }

  function renderChunks(chunks, pages) {
    chunksEl.innerHTML = "";
    if (!chunks.length) {
      const msg = document.createElement("p");
      msg.className = "viewer-empty";
      msg.textContent = "No chunks yet (HTML/images are indexed without chunks in A2).";
      chunksEl.appendChild(msg);
      return;
    }
    for (const chunk of chunks) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "chunk-card";
      card.dataset.chunkId = String(chunk.id);
      const loc = chunk.locator || {};
      const where =
        loc.type === "pdf"
          ? `p.${loc.page ?? chunk.page_start ?? "?"}`
          : loc.type === "pptx"
            ? `slide ${loc.slide_start ?? chunk.page_start ?? "?"}-${loc.slide_end ?? loc.slide_start ?? chunk.page_end ?? "?"}`
            : loc.type === "docx"
              ? `¶ ${loc.paragraph_start}-${loc.paragraph_end}`
              : loc.type === "note"
                ? `lines ${loc.line_start}-${loc.line_end}`
                : loc.type || "chunk";
      card.innerHTML = `
        <div class="chunk-meta">
          <span>#${chunk.ordinal}</span>
          <span>${escapeHtml(where)}</span>
          ${chunk.heading_path ? `<span class="chunk-path">${escapeHtml(chunk.heading_path)}</span>` : ""}
        </div>
        <pre class="chunk-text">${escapeHtml(chunk.text)}</pre>
      `;
      card.addEventListener("click", () => selectChunk(chunk, pages));
      chunksEl.appendChild(card);
    }
  }

  async function load() {
    try {
      const [doc, pagesRes, chunksRes] = await Promise.all([
        getDocument(docId),
        getDocumentPages(docId),
        getDocumentChunks(docId),
      ]);
      titleEl.textContent = doc.title;
      metaEl.textContent = `${doc.kind} · ${doc.status}${doc.error ? ` · ${doc.error}` : ""}`;
      const pages = pagesRes.pages || [];
      const chunks = chunksRes.chunks || [];
      renderPages(doc, pages, null, null);
      renderChunks(chunks, pages);
    } catch (err) {
      titleEl.textContent = "Document";
      showError(`Could not load document: ${err.message}`);
    }
  }

  load();

  return {
    unmount: () => {
      container.innerHTML = "";
    },
  };
}
