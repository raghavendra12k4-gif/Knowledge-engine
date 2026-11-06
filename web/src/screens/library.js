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

  duplicateOpen.addEventListener("click", () => {
    if (pendingDuplicateDoc) {
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
        if (res.job_id) {
          subscribeJobEvents(res.job_id, (ev) => {
            const fill = container.querySelector(`#fill-${res.document.id}`);
            if (fill && ev.progress != null) fill.style.width = `${ev.progress}%`;
            if (ev.status) {
              const chip = container.querySelector(`tr[data-doc="${res.document.id}"] .status-chip`);
              // reload documents on terminal status
              if (ev.status === "ready" || ev.status === "failed" || ev.status === "needs_review") {
                loadDocuments();
              }
            }
          });
        }
      }
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    }
  }

  async function loadDocuments() {
    const data = await listDocuments();
    renderDocuments(data.documents || []);
  }

  function renderDocuments(docs) {
    docList.innerHTML = "";
    emptyState.style.display = docs.length ? "none" : "block";

    for (const doc of docs) {
      const row = document.createElement("tr");
      const statusClass = STATUS_CLASSES[doc.status] || "status-queued";
      const label = STATUS_LABELS[doc.status] || doc.status;

      row.innerHTML = `
        <td class="doc-title">${escapeHtml(doc.title)}</td>
        <td>${escapeHtml(doc.kind)}</td>
        <td><span class="status-chip ${statusClass}">${label}</span></td>
        <td>
          <div class="progress-bar" id="progress-${doc.id}">
            <div class="progress-fill" id="fill-${doc.id}" style="width:0%"></div>
          </div>
        </td>
        <td class="doc-actions">
          <button class="btn btn-link btn-retry" data-id="${doc.id}" ${doc.status !== "failed" ? "disabled" : ""}>Retry</button>
          <button class="btn btn-link btn-delete" data-id="${doc.id}">Delete</button>
        </td>
      `;

      docList.appendChild(row);

      // Subscribe to job events if there's a recent job for this document.
      // We poll the document list to find the latest job, but for now
      // we'll just show the current status. SSE subscription happens
      // when a new upload creates a job.
    }
  }

  // Event delegation for action buttons
  docList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.classList.contains("btn-delete")) {
      if (!confirm("Delete this document?")) return;
      try {
        await deleteDocument(id);
        await loadDocuments();
      } catch (err) {
        alert(`Delete failed: ${err.message}`);
      }
    } else if (btn.classList.contains("btn-retry")) {
      try {
        await reprocessDocument(id);
        await loadDocuments();
      } catch (err) {
        alert(`Reprocess failed: ${err.message}`);
      }
    }
  });

  // Initial load
  loadDocuments();

  return {
    unmount: () => {
      container.innerHTML = "";
    },
  };
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}
