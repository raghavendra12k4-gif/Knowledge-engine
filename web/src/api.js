const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
}

export const health = () => getJSON(`${BASE}/health`);

export const listDocuments = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return getJSON(`${BASE}/documents${qs ? `?${qs}` : ""}`);
};

export const getDocument = (id) => getJSON(`${BASE}/documents/${id}`);
export const getDocumentChunks = (id) => getJSON(`${BASE}/documents/${id}/chunks`);
export const getDocumentPages = (id) => getJSON(`${BASE}/documents/${id}/pages`);
export const pageImageUrl = (docId, pageNo) =>
  `${BASE}/documents/${docId}/pages/${pageNo}/image`;

export const deleteDocument = (id) =>
  fetch(`${BASE}/documents/${id}`, { method: "DELETE" }).then((res) => {
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  });

export const reprocessDocument = (id) =>
  postJSON(`${BASE}/documents/${id}/reprocess`, {});

export const uploadDocument = (file) => {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${BASE}/documents/upload`, {
    method: "POST",
    body: form,
  }).then(async (res) => {
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
      throw new Error(detail);
    }
    return res.json();
  });
};

export const subscribeJobEvents = (jobId, onEvent) => {
  const es = new EventSource(`${BASE}/jobs/${jobId}/events`);
  const handler = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      onEvent(e.data);
    }
  };
  for (const name of ["state", "started", "progress", "completed", "failed"]) {
    es.addEventListener(name, handler);
  }
  es.onmessage = handler;
  es.onerror = () => es.close();
  return es;
};
