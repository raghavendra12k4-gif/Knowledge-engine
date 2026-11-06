import "./styles/tokens.css";
import { health } from "./api.js";

const el = document.getElementById("server-status");

health()
  .then((h) => {
    el.textContent = `Server OK (schema v${h.schema_version})`;
    el.classList.add("ok");
  })
  .catch((err) => {
    el.textContent = `Server unreachable: ${err.message}`;
    el.classList.add("bad");
  });
