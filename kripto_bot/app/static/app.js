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
  // A "?" icon whose tooltip explains a setting (id: to change the text later with setTip).
  tip(text, id = "") {
    return `<i class="bi bi-question-circle text-secondary ms-1" role="button" tabindex="0"${id ? ` id="${id}"` : ""}
      data-bs-toggle="tooltip" data-bs-title="${KB.esc(text)}"></i>`;
  },
  // Create the Bootstrap tooltips under `root` (after its HTML changed).
  initTips(root = document) {
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => bootstrap.Tooltip.getOrCreateInstance(el));
  },
  disposeTips(root) {
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => bootstrap.Tooltip.getInstance(el)?.dispose());
  },
  setTip(el, text) {
    if (typeof el === "string") el = document.getElementById(el);
    bootstrap.Tooltip.getInstance(el)?.dispose();
    el.setAttribute("data-bs-title", text);
    el.classList.toggle("d-none", !text);
    bootstrap.Tooltip.getOrCreateInstance(el);
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
  // Render rows into a table: cols = [[header, row => html, row => sort value (optional)], ...].
  // Clicking the header of a column with a sort value sorts by it (again: reverses). `sort` is
  // the initial order, [column index, "asc" | "desc"]; a clicked order survives re-renders.
  table(el, rows, cols, empty = "Nothing here yet.", sort = null) {
    el._sort = el._sort || sort;
    el._args = [rows, cols, empty];
    const s = el._sort, key = s && cols[s[0]] && cols[s[0]][2];
    if (key) {
      const dir = s[1] === "asc" ? 1 : -1;
      rows = [...rows].sort((a, b) => {          // stable: ties keep the server's order
        const x = key(a), y = key(b);
        if (x == null || y == null) return (x == null) - (y == null);   // missing values last
        return (x < y ? -1 : x > y ? 1 : 0) * dir;
      });
    }
    const head = cols.map((c, i) => {
      if (!c[2]) return `<th>${c[0]}</th>`;
      const icon = s && s[0] === i ? (s[1] === "asc" ? "bi-caret-up-fill" : "bi-caret-down-fill")
                                   : "bi-chevron-expand text-body-tertiary";
      return `<th role="button" class="user-select-none" data-sort="${i}">${c[0]} <i class="bi ${icon} small"></i></th>`;
    }).join("");
    KB.disposeTips(el);        // tooltips of the rows being replaced
    el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>` +
      (rows.length ? rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")
                   : `<tr><td colspan="${cols.length}" class="text-muted">${empty}</td></tr>`) + "</tbody>";
    KB.initTips(el);
    if (!el._sortable) {
      el._sortable = true;
      el.addEventListener("click", e => {
        const th = e.target.closest("th[data-sort]");
        if (!th) return;
        const i = Number(th.dataset.sort), cur = el._sort;
        el._sort = [i, cur && cur[0] === i && cur[1] === "asc" ? "desc" : "asc"];
        KB.table(el, ...el._args);
      });
    }
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
