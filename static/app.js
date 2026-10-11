// Shared helpers for the control panel pages.
const KB = {
  // JSON API call; mutating requests carry the CSRF header the server requires.
  async api(url, opts = {}) {
    const o = { ...opts, headers: { "Content-Type": "application/json", "X-Requested-With": "kripto-bot" } };
    if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
    const r = await fetch(url, o);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  },
  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  num(v, d = 2) { return v == null || v === "" || isNaN(v) ? "–" : Number(v).toFixed(d); },
  pct(v, d = 2) { return v == null || isNaN(v) ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%"; },
  sign(v) { return v == null || isNaN(v) ? "" : v >= 0 ? "text-success" : "text-danger"; },
  time(t) { return t ? String(t).replace("T", " ").slice(0, 16) : "–"; },
  params(p) { const e = Object.entries(p || {}); return e.length ? e.map(([k, v]) => `${k}=${v}`).join(", ") : "defaults"; },
  statusBadge(st, enabled) {
    const s = (st && st.state) || "stopped";
    const map = { running: "success", "waiting to restart": "warning", starting: "info", blocked: "danger",
                  stopped: "secondary", "supervisor not running": enabled ? "warning" : "secondary" };
    const label = s === "supervisor not running" && !enabled ? "stopped" : s;
    const note = st && st.note ? ` title="${KB.esc(st.note)}"` : "";
    return `<span class="badge text-bg-${map[s] || "secondary"}"${note}>${KB.esc(label)}</span>`;
  },
  modeBadge(m) { return `<span class="badge ${m === "testnet" ? "text-bg-warning" : "text-bg-primary"}">${KB.esc(m)}</span>`; },
  toast(msg, kind = "success") {
    const box = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = `toast align-items-center text-bg-${kind} border-0 show mb-2`;
    el.innerHTML = `<div class="d-flex"><div class="toast-body">${KB.esc(msg)}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="this.closest('.toast').remove()"></button></div>`;
    box.appendChild(el);
    setTimeout(() => el.remove(), kind === "danger" ? 8000 : 4000);
  },
  // Render rows into a table body: cols = [[header, row => html], ...]
  table(el, rows, cols, empty = "Nothing here yet.") {
    el.innerHTML = `<thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>` +
      (rows.length ? rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")
                   : `<tr><td colspan="${cols.length}" class="text-muted">${empty}</td></tr>`) + "</tbody>";
  },
  cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); },
};

// Light/dark theme toggle (remembered per browser).
(function () {
  const saved = localStorage.getItem("kb-theme");
  if (saved) document.documentElement.setAttribute("data-bs-theme", saved);
  window.toggleTheme = () => {
    const next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    localStorage.setItem("kb-theme", next);
    window.dispatchEvent(new Event("kb-theme"));
  };
})();
