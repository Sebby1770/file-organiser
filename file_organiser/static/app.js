const pathInput = document.querySelector("#path");
const statusEl = document.querySelector("#status");
const canvas = document.querySelector("#sun");
const tip = document.querySelector("#tip");
const sessionToken = document.querySelector('meta[name="file-organiser-session"]').content;
const inspect = {
  name: document.querySelector("#i-name"),
  size: document.querySelector("#i-size"),
  meta: document.querySelector("#i-meta"),
  actions: document.querySelector("#i-actions"),
  why: document.querySelector("#i-why"),
};
let scan = null;
let advice = null;
let slices = [];
let treeHits = [];
let selected = null;
let pollTimer = null;
let view = "sun";
let viewRoot = null;
const zoomStack = [];

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-File-Organiser-Token", sessionToken);
  return fetch(url, { ...options, headers, credentials: "same-origin" });
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmt(n) {
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " TB";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " KB";
  return n + " B";
}

function showWorkspace() {
  document.querySelector("#welcome").hidden = true;
  document.querySelector("#workspace").hidden = false;
}

async function loadRoots() {
  const data = await apiFetch("/api/roots").then((r) => r.json());
  const rail = document.querySelector("#roots");
  rail.innerHTML = (data.roots || [])
    .map((r) => `<button type="button" data-path="${esc(r.path)}">${esc(r.label)}</button>`)
    .join("");
  rail.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      pathInput.value = btn.dataset.path;
      startScan(btn.dataset.path);
    });
  });
  return data.roots || [];
}

function rootByLabel(roots, label) {
  return (roots || []).find((r) => r.label === label);
}

function showInspect(node) {
  selected = node;
  inspect.name.textContent = node.name || node.path;
  inspect.size.textContent = node.label || fmt(node.size || 0);
  inspect.meta.textContent = `${node.dir ? "folder" : "file"} · ${node.files || 0} files\n${node.path || ""}`;
  inspect.actions.hidden = !node.path;
  document.querySelector("#trash").hidden = !node.safeDelete;
  inspect.why.hidden = true;
}

function currentTree() {
  return viewRoot || (scan && scan.tree);
}

function paint() {
  const tree = currentTree();
  if (!tree) return;
  if (!tree.label && tree.size) tree.label = fmt(tree.size);
  if (view === "tree") {
    treeHits = drawTreemap(canvas, tree, selected && selected.path);
    slices = [];
  } else {
    slices = drawSunburst(canvas, tree, selected && selected.path);
    treeHits = [];
  }
  const bits = [scan.root, scan.visited + " files"];
  if (viewRoot && viewRoot.path !== scan.root) bits.push("zoomed " + viewRoot.name);
  document.querySelector("#crumb").textContent = bits.join(" · ");
}

function table(rows, cols) {
  if (!rows.length) return "<p class='meta'>Nothing here.</p>";
  const head = "<tr>" + cols.map((c) => `<th>${esc(c.label)}</th>`).join("") + "</tr>";
  const body = rows
    .map((row) => {
      const tds = cols.map((c) => `<td>${c.html ? c.render(row) : esc(c.render(row))}</td>`).join("");
      return `<tr data-path="${esc(row.path || "")}">${tds}</tr>`;
    })
    .join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function adviceRow(item, checkable) {
  const box = checkable
    ? `<input type="checkbox" checked data-path="${esc(item.path)}" />`
    : "";
  return `<div class="row" data-path="${esc(item.path)}">
    ${box}
    <span class="sz">${esc(item.label)}</span>
    <span class="nm">${esc(item.name)}</span>
    <p class="why-line">${esc(item.reason)}</p>
  </div>`;
}

function fillAdvice(payload) {
  advice = payload;
  if (!payload || !payload.summary) return;
  const s = payload.summary;
  document.querySelector("#headline").textContent = s.headline;
  document.querySelector("#sum-delete").textContent = `${s.delete_label} · ${s.delete_n} items`;
  document.querySelector("#sum-review").textContent = `${s.review_label} · ${s.review_n} items`;
  document.querySelector("#sum-keep").textContent = `${s.keep_label} · ${s.keep_n} items`;
  document.querySelector("#list-delete").innerHTML = (payload.delete || []).map((i) => adviceRow(i, true)).join("") || "<p class='meta'>Nothing safe to delete.</p>";
  document.querySelector("#list-review").innerHTML = (payload.review || []).map((i) => adviceRow(i, false)).join("") || "<p class='meta'>Nothing to review.</p>";
  document.querySelector("#list-keep").innerHTML = (payload.keep || []).map((i) => adviceRow(i, false)).join("") || "<p class='meta'>No protected libraries in this folder.</p>";
  document.querySelectorAll(".board .row[data-path]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      showInspect({
        name: row.querySelector(".nm").textContent,
        path: row.dataset.path,
        size: 0,
        label: row.querySelector(".sz").textContent,
        files: 1,
        dir: false,
        safeDelete: row.closest(".delete") !== null,
      });
    });
  });
}

function fillTabs() {
  if (!scan) return;
  document.querySelector("#tab-largest").innerHTML = table(scan.largest || [], [
    { label: "Name", render: (r) => r.name },
    { label: "Size", render: (r) => r.label },
    { label: "Type", render: (r) => r.category },
  ]);
  document.querySelector("#tab-stale").innerHTML = table(scan.stale || [], [
    { label: "Name", render: (r) => r.name },
    { label: "Size", render: (r) => r.label },
    { label: "Type", render: (r) => r.category },
  ]);
  const maxCat = Math.max(1, ...(scan.categories || []).map((c) => c.size));
  document.querySelector("#tab-cats").innerHTML = table(scan.categories || [], [
    { label: "Category", render: (r) => r.category },
    { label: "Size", html: true, render: (r) => `<span class="bar" style="width:${Math.max(8, (Number(r.size) / maxCat) * 180)}px"></span>${esc(r.label)}` },
    { label: "Files", render: (r) => r.files },
  ]);
  document.querySelectorAll("#tab-largest tr[data-path], #tab-stale tr[data-path]").forEach((tr) => {
    tr.addEventListener("click", () => {
      showInspect({
        name: tr.children[0].textContent,
        path: tr.dataset.path,
        size: 0,
        label: tr.children[1].textContent,
        files: 1,
        dir: false,
      });
    });
  });
  const w = scan.waste || {};
  const v = scan.volume || {};
  const volEl = document.querySelector("#vol");
  const wasteEl = document.querySelector("#waste");
  if (v.free_label) {
    volEl.hidden = false;
    volEl.textContent = `Disk ${v.pct}% used · ${v.free_label} free of ${v.total_label}`;
  }
  if (w.total) {
    wasteEl.hidden = false;
    wasteEl.innerHTML = `<strong>Reclaim ~ ${esc(w.total_label)}</strong><br>caches ${esc(w.caches_label)} · stale ${esc(w.stale_label)} · ${esc(w.empty)} empty folders`;
  }
  document.querySelector("#tab-reclaim").innerHTML = `
    <p class="meta">Caches plus large files untouched for a year. The Advice board above is the decision list — this is the raw reclaim estimate.</p>
    <p><strong>${esc(w.total_label || "0B")}</strong> likely reclaimable</p>
    <ul class="meta">
      <li>Build/cache folders: ${esc(w.caches_label || "0")} (${esc(w.cache_files || 0)} files)</li>
      <li>Stale large files: ${esc(w.stale_label || "0")}</li>
      <li>Empty folders: ${esc(w.empty || 0)}</li>
    </ul>`;
}

async function poll() {
  const st = await apiFetch("/api/status").then((r) => r.json());
  const bar = document.querySelector("#progress");
  const inner = document.querySelector("#pbar");
  if (st.status === "running") {
    statusEl.textContent = `Scanning… ${st.visited} files · ${st.label}`;
    bar.hidden = false;
    inner.style.width = Math.min(90, 12 + (st.visited % 400) / 8) + "%";
    return;
  }
  bar.hidden = true;
  clearInterval(pollTimer);
  pollTimer = null;
  const data = await apiFetch("/api/scan").then((r) => r.json());
  if (data.error) {
    statusEl.textContent = data.error;
    return;
  }
  scan = data.result;
  advice = data.advice;
  viewRoot = scan.tree;
  zoomStack.length = 0;
  statusEl.textContent = `Mapped ${scan.files} files · ${scan.label} in ${scan.elapsed_ms} ms`;
  showWorkspace();
  showInspect(scan.tree);
  paint();
  fillTabs();
  if (advice) fillAdvice(advice);
}

async function startScan(path) {
  if (!path) return;
  showWorkspace();
  statusEl.textContent = "Starting scan…";
  document.querySelector("#progress").hidden = false;
  document.querySelector("#pbar").style.width = "8%";
  const res = await apiFetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (!data.ok) {
    statusEl.textContent = data.error || "scan failed";
    return;
  }
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(poll, 350);
  poll();
}

async function zoomInto(node) {
  if (!node || !node.dir || !node.path) return;
  if (node.children && node.children.length) {
    zoomStack.push(viewRoot);
    viewRoot = node;
    showInspect(node);
    paint();
    return;
  }
  const data = await apiFetch("/api/zoom", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: node.path }),
  }).then((r) => r.json());
  if (!data.ok) {
    statusEl.textContent = data.error;
    return;
  }
  zoomStack.push(viewRoot);
  viewRoot = data.tree;
  showInspect(viewRoot);
  paint();
}

function zoomOut() {
  if (zoomStack.length) {
    viewRoot = zoomStack.pop();
    showInspect(viewRoot);
    paint();
  }
}

async function pickFolder() {
  const data = await apiFetch("/api/pick", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((r) => r.json());
  if (data.ok && data.path) {
    pathInput.value = data.path;
    startScan(data.path);
  }
}

document.querySelector("#path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startScan(pathInput.value.trim());
});

document.querySelector("#browse").addEventListener("click", pickFolder);
document.querySelector("#welcome-browse").addEventListener("click", pickFolder);

document.querySelector("#cancel").addEventListener("click", () => {
  apiFetch("/api/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
});

document.querySelector("#scan-computer").addEventListener("click", async () => {
  const roots = await apiFetch("/api/roots").then((r) => r.json());
  const home = rootByLabel(roots.roots, "Home folder") || (roots.roots || [])[0];
  if (!home) return;
  pathInput.value = home.path;
  startScan(home.path);
});

document.querySelector("#scan-downloads").addEventListener("click", async () => {
  const roots = await apiFetch("/api/roots").then((r) => r.json());
  const dl = rootByLabel(roots.roots, "Downloads");
  if (!dl) {
    statusEl.textContent = "No Downloads folder on this computer.";
    return;
  }
  pathInput.value = dl.path;
  startScan(dl.path);
});

document.querySelector("#zoom-out").addEventListener("click", zoomOut);
document.querySelector("#view-sun").addEventListener("click", () => {
  view = "sun";
  paint();
});
document.querySelector("#view-tree").addEventListener("click", () => {
  view = "tree";
  paint();
});

canvas.addEventListener("click", (event) => {
  const node = view === "tree" ? hitTreemap(treeHits, canvas, event.clientX, event.clientY) : hitSunburst(slices, canvas, event.clientX, event.clientY);
  if (node) {
    showInspect(node);
    paint();
  }
});
canvas.addEventListener("dblclick", (event) => {
  const node = view === "tree" ? hitTreemap(treeHits, canvas, event.clientX, event.clientY) : hitSunburst(slices, canvas, event.clientX, event.clientY);
  if (node) zoomInto(node);
});
canvas.addEventListener("mousemove", (event) => {
  const node = view === "tree" ? hitTreemap(treeHits, canvas, event.clientX, event.clientY) : hitSunburst(slices, canvas, event.clientX, event.clientY);
  if (!node) {
    tip.hidden = true;
    return;
  }
  tip.hidden = false;
  tip.style.left = event.clientX + 12 + "px";
  tip.style.top = event.clientY + 12 + "px";
  tip.textContent = `${node.name} · ${node.label || fmt(node.size || 0)}`;
});
canvas.addEventListener("mouseleave", () => {
  tip.hidden = true;
});

document.querySelector("#reveal").addEventListener("click", () => {
  if (!selected || !selected.path) return;
  apiFetch("/api/reveal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selected.path }) });
});
document.querySelector("#copy").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  await navigator.clipboard.writeText(selected.path);
  statusEl.textContent = "Path copied.";
});
document.querySelector("#trash").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  if (!confirm(`Move to trash?\n${selected.path}`)) return;
  const data = await apiFetch("/api/trash", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: selected.path }),
  }).then((r) => r.json());
  statusEl.textContent = data.ok ? `${data.action} ${selected.name}` : data.error;
  if (data.ok) startScan(pathInput.value.trim());
});
document.querySelector("#why").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  const data = await apiFetch("/api/why?path=" + encodeURIComponent(selected.path)).then((r) => r.json());
  inspect.why.hidden = false;
  inspect.why.textContent = (data.why && data.why.why ? data.why.why.join("\n") : data.error) || "";
});

document.querySelector("#trash-checked").addEventListener("click", async () => {
  const boxes = [...document.querySelectorAll("#list-delete input[type=checkbox]:checked")];
  const paths = boxes.map((b) => b.dataset.path).filter(Boolean);
  if (!paths.length) {
    statusEl.textContent = "Nothing checked.";
    return;
  }
  if (!confirm(`Move ${paths.length} item(s) to Trash?\nThis only includes the Delete column.`)) return;
  const res = await apiFetch("/api/advise/purge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  }).then((r) => r.json());
  statusEl.textContent = res.ok ? `Trashed ${res.n} · skipped ${ (res.skipped || []).length }` : res.error;
  if (res.ok) startScan(pathInput.value.trim());
});

document.querySelector("#tabs").addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === btn));
  document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("on", el.id === "tab-" + btn.dataset.tab));
});

document.querySelector('[data-tab="dupes"]').addEventListener("click", async () => {
  if (!scan) return;
  document.querySelector("#tab-dupes").innerHTML = "<p class='meta'>Hashing…</p>";
  const data = await apiFetch("/api/dupes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: scan.root, min_size: 256000 }),
  }).then((r) => r.json());
  if (!data.ok) {
    document.querySelector("#tab-dupes").textContent = data.error;
    return;
  }
  const groups = data.groups || [];
  const html =
    `<p class="meta">Potentially reclaimable ${esc(data.reclaim_label)} across ${esc(data.extra_files)} extra copies. Every file is re-hashed before Trash.</p>` +
    groups
      .map(
        (g) =>
          `<div class="dupe"><p><strong>${esc(g.count)}×</strong> keep ${esc(g.keep.split("/").pop())}</p>
           <button class="btn danger" data-group="${esc(g.group)}" data-keep="${esc(g.keep)}">Trash safe extras</button>
           <p class="meta">${g.files.map((f) => esc(f.name + " " + f.label)).join(" · ")}</p></div>`
      )
      .join("");
  document.querySelector("#tab-dupes").innerHTML = html || "<p class='meta'>No duplicates over 256 KB.</p>";
  document.querySelectorAll("#tab-dupes [data-group]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Move duplicate extras to Trash only if they are also on the Delete list? Files are re-checked first.")) return;
      const res = await apiFetch("/api/dupes/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep: btn.dataset.keep, group: btn.dataset.group }),
      }).then((r) => r.json());
      statusEl.textContent = res.ok ? `Trashed ${res.n} extras` : res.error;
      if (res.ok) startScan(pathInput.value.trim());
    });
  });
});

document.querySelector('[data-tab="plan"]').addEventListener("click", async () => {
  if (!scan) return;
  document.querySelector("#tab-plan").innerHTML = "<p class='meta'>Planning…</p>";
  const data = await apiFetch("/api/organize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: scan.root, recursive: false, magic: true, smart: true }),
  }).then((r) => r.json());
  if (!data.ok) {
    document.querySelector("#tab-plan").textContent = data.error;
    return;
  }
  const files = (data.plan && data.plan.files) || [];
  document.querySelector("#tab-plan").innerHTML =
    `<p class="meta">${esc(data.plan.count)} files would move into category folders.</p>
     <button class="btn" id="apply-plan" type="button">Apply organize</button>` +
    table(
      files.slice(0, 40).map((f) => ({
        name: f.source.split("/").pop(),
        path: f.source,
        category: f.category,
      })),
      [
        { label: "File", render: (r) => r.name },
        { label: "Category", render: (r) => r.category },
      ]
    );
  const apply = document.querySelector("#apply-plan");
  if (apply) {
    apply.addEventListener("click", async () => {
      if (!confirm("Move files into category folders? Undo is available from the CLI.")) return;
      const res = await apiFetch("/api/organize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: scan.root, recursive: false, apply: true }),
      }).then((r) => r.json());
      statusEl.textContent = res.ok ? `Moved ${res.moved} files` : res.error;
      if (res.ok) startScan(pathInput.value.trim());
    });
  }
});

async function exportReport(format) {
  if (!scan) {
    statusEl.textContent = "Scan a folder before exporting a report.";
    return;
  }
  const response = await apiFetch(`/api/report?format=${encodeURIComponent(format)}`);
  if (!response.ok) {
    const data = await response.json();
    statusEl.textContent = data.error || "Could not export the report.";
    return;
  }
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = `file-organiser-scan.${format}`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
  statusEl.textContent = `Exported a local ${format.toUpperCase()} report. Paths in it may be sensitive.`;
}

document.querySelector("#export-json").addEventListener("click", () => exportReport("json"));
document.querySelector("#export-csv").addEventListener("click", () => exportReport("csv"));

document.addEventListener("keydown", (event) => {
  if (event.target && ["INPUT", "TEXTAREA"].includes(event.target.tagName)) return;
  if (event.key === "Backspace") {
    event.preventDefault();
    zoomOut();
  }
  if (event.key === "Enter" && pathInput.value) startScan(pathInput.value.trim());
  if (event.key === "b" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    document.querySelector("#browse").click();
  }
});

document.body.addEventListener("dragover", (event) => event.preventDefault());
document.body.addEventListener("drop", (event) => {
  event.preventDefault();
  const file = event.dataTransfer.files[0];
  if (file && file.path) {
    pathInput.value = file.path;
    startScan(file.path);
  }
});

loadRoots().then(async (roots) => {
  const st = await apiFetch("/api/status").then((r) => r.json());
  if (st.root) {
    pathInput.value = st.root;
    if (st.status === "done" || st.status === "running") {
      showWorkspace();
      if (!pollTimer) pollTimer = setInterval(poll, 350);
      poll();
      return;
    }
  }
  const home = rootByLabel(roots, "Home folder");
  if (home && !pathInput.value) pathInput.value = home.path;
});
