import {
  deleteDocument,
  getDocument,
  listDocuments,
  reprocessDocument,
  subscribeJobEvents,
  uploadDocument,
} from "../api.js";

const STATUS_LABELS = {
  queued: "Queued",
  processing: "Processing",
  ready: "Ready",
  needs_review: "Needs Review",
  failed: "Failed",
};

const STATUS_CLASSES = {
  queued: "status-queued",
  processing: "status-processing",
  ready: "status-ready",
  needs_review: "status-needs-review",
  failed: "status-failed",
};

export function mountLibrary(container) {
  container.innerHTML = `
    <div class="library">
      <div class="library-header">
        <h2>Library</h2>
        <div class="upload-zone" id="drop-zone">
          <p>Drag &amp; drop files here, or click to browse</p>
          <input type="file" id="file-input" multiple hidden />
          <button class="btn btn-primary" id="browse-btn">Browse</button>
        </div>
        <div id="duplicate-notice" class="notice notice-info" style="display:none;">
          <strong>Duplicate detected:</strong> <span id="duplicate-title"></span>
          <button class="btn btn-link" id="duplicate-open">Open existing</button>
        </div>
        <p id="library-error" class="library-error" role="alert" hidden></p>
      </div>

      <div class="library-table-wrap">
        <table class="library-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="doc-list"></tbody>
        </table>
      </div>

      <div id="empty-state" class="empty-state" style="display:none;">
        <p>No documents yet. Drop files here or copy them into <code>data/inbox/</code>.</p>
      </div>
      <dialog id="document-dialog" class="document-dialog">
        <form method="dialog" class="dialog-close">
          <button class="btn btn-link" aria-label="Close document details">Close</button>
        </form>
        <h3 id="detail-title"></h3>
        <dl>
          <dt>Type</dt><dd id="detail-kind"></dd>
          <dt>Status</dt><dd id="detail-status"></dd>
          <dt>Error</dt><dd id="detail-error"></dd>
          <dt>Added</dt><dd id="detail-created"></dd>
        </dl>
      </dialog>
    </div>
  `;

  const dropZone = container.querySelector("#drop-zone");
  const fileInput = container.querySelector("#file-input");
  const browseBtn = container.querySelector("#browse-btn");
  const docList = container.querySelector("#doc-list");
  const emptyState = container.querySelector("#empty-state");
  const duplicateNotice = container.querySelector("#duplicate-notice");
  const duplicateTitle = container.querySelector("#duplicate-title");
  const duplicateOpen = container.querySelector("#duplicate-open");
  const libraryError = container.querySelector("#library-error");
  const detailDialog = container.querySelector("#document-dialog");
  const detailTitle = container.querySelector("#detail-title");
  const detailKind = container.querySelector("#detail-kind");
  const detailStatus = container.querySelector("#detail-status");
  const detailError = container.querySelector("#detail-error");
  const detailCreated = container.querySelector("#detail-created");

  const activeSources = new Map();
  let pendingDuplicateDoc = null;

  browseBtn.addEventListener("click", () => fileInput.click());

  // Drag & drop
  dropZone.addEventListener("click", (e) => {
    if (e.target === dropZone || e.target.closest("p")) fileInput.click();
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
  });

  dropZone.addEventListener("drop", async (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    const files = Array.from(e.dataTransfer.files);
    for (const f of files) await handleFile(f);
  });

  fileInput.addEventListener("change", async (e) => {
    for (const f of Array.from(e.target.files)) await handleFile(f);
    e.target.value = "";
  });

  duplicateOpen.addEventListener("click", async () => {
    if (pendingDuplicateDoc) {
      try {
        await openDocument(pendingDuplicateDoc.id);
      } catch (err) {
        showError(`Could not open document: ${err.message}`);
      }
      duplicateNotice.style.display = "none";
      pendingDuplicateDoc = null;
    }
  });

  async function handleFile(file) {
    try {
      const res = await uploadDocument(file);
      if (res.duplicate) {
        pendingDuplicateDoc = res.document;
        duplicateTitle.textContent = res.document.title;
        duplicateNotice.style.display = "block";
      } else {
        await loadDocuments();
      }
    } catch (err) {
      showError(`Upload failed: ${err.message}`);
    }
  }

  async function loadDocuments() {
    const data = await listDocuments();
    libraryError.hidden = true;
    renderDocuments(data.documents || []);
    for (const doc of data.documents || []) {
      if (doc.job_id && ["queued", "processing"].includes(doc.status)) {
        trackJob(doc.id, doc.job_id);
      }
    }
  }

  function showError(message) {
    libraryError.textContent = message;
    libraryError.hidden = false;
  }

  function trackJob(docId, jobId) {
    if (activeSources.has(jobId)) return;
    const source = subscribeJobEvents(jobId, (event) => {
      const fill = container.querySelector(`#fill-${docId}`);
      if (fill && event.progress != null) {
        fill.style.width = `${Math.max(0, Math.min(100, event.progress))}%`;
      }
      if (
        event.kind === "completed" ||
        event.kind === "failed" ||
        ["done", "failed", "cancelled"].includes(event.status)
      ) {
        source.close();
        activeSources.delete(jobId);
      }
      loadDocuments().catch((err) => showError(`Could not refresh library: ${err.message}`));
    });
    activeSources.set(jobId, source);
  }

  async function openDocument(id) {
    const doc = await getDocument(id);
    detailTitle.textContent = doc.title;
    detailKind.textContent = doc.kind;
    detailStatus.textContent = doc.status;
    detailError.textContent = doc.error || "None";
    detailCreated.textContent = doc.created_at;
    detailDialog.showModal();
  }

  function renderDocuments(docs) {
    docList.innerHTML = "";
    emptyState.style.display = docs.length ? "none" : "block";

    for (const doc of docs) {
      const row = document.createElement("tr");
      row.dataset.doc = doc.id;
      const statusClass = STATUS_CLASSES[doc.status] || "status-queued";
      const label = STATUS_LABELS[doc.status] || doc.status;

      row.innerHTML = `
        <td class="doc-title"><button class="btn btn-link btn-open" data-id="${doc.id}">${escapeHtml(doc.title)}</button></td>
        <td>${escapeHtml(doc.kind)}</td>
        <td>
          <span class="status-chip ${statusClass}">${label}</span>
          ${doc.error ? `<small class="doc-error">${escapeHtml(doc.error)}</small>` : ""}
        </td>
        <td>
          <div class="progress-bar" id="progress-${doc.id}">
            <div class="progress-fill" id="fill-${doc.id}" style="width:${Math.max(0, Math.min(100, doc.job_progress || 0))}%"></div>
          </div>
        </td>
        <td class="doc-actions">
          <button class="btn btn-link btn-retry" data-id="${doc.id}" ${doc.status !== "failed" ? "disabled" : ""}>Retry</button>
          <button class="btn btn-link btn-delete" data-id="${doc.id}">Delete</button>
        </td>
      `;

      docList.appendChild(row);
    }
  }

  // Event delegation for action buttons
  docList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.classList.contains("btn-open")) {
      try {
        await openDocument(id);
      } catch (err) {
        showError(`Could not open document: ${err.message}`);
      }
    } else if (btn.classList.contains("btn-delete")) {
      if (!confirm("Delete this document?")) return;
      try {
        await deleteDocument(id);
        await loadDocuments();
      } catch (err) {
        showError(`Delete failed: ${err.message}`);
      }
    } else if (btn.classList.contains("btn-retry")) {
      try {
        await reprocessDocument(id);
        await loadDocuments();
      } catch (err) {
        showError(`Reprocess failed: ${err.message}`);
      }
    }
  });

  const refreshTimer = window.setInterval(() => {
    loadDocuments().catch((err) => showError(`Could not refresh library: ${err.message}`));
  }, 2000);
  loadDocuments().catch((err) => showError(`Could not load library: ${err.message}`));

  return {
    unmount: () => {
      window.clearInterval(refreshTimer);
      activeSources.forEach((es) => es.close());
      activeSources.clear();
      container.innerHTML = "";
    },
  };
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}
