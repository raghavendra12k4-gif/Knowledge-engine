import "./styles/tokens.css";
import { health } from "./api.js";
import { mountLibrary } from "./screens/library.js";

// Minimal client-side router
const app = document.getElementById("app");
let currentMount = null;

function renderHome() {
  app.innerHTML = `
    <div class="home">
      <h1>Knowledge Engine</h1>
      <p id="server-status" class="status">Checking server…</p>
      <nav class="home-nav">
        <a href="#/library" class="btn btn-primary">Go to Library</a>
      </nav>
    </div>
  `;
  health()
    .then((h) => {
      const statusEl = document.getElementById("server-status");
      if (statusEl) {
        statusEl.textContent = `Server OK (schema v${h.schema_version})`;
        statusEl.classList.add("ok");
      }
    })
    .catch((err) => {
      const statusEl = document.getElementById("server-status");
      if (statusEl) {
        statusEl.textContent = `Server unreachable: ${err.message}`;
        statusEl.classList.add("bad");
      }
    });
}

function unmountCurrent() {
  if (currentMount) {
    currentMount.unmount();
    currentMount = null;
  }
}

function navigate(path) {
  unmountCurrent();
  if (path === "#/library") {
    currentMount = mountLibrary(app);
  } else {
    renderHome();
  }
}

window.addEventListener("hashchange", () => navigate(location.hash));
navigate(location.hash || "#/");
