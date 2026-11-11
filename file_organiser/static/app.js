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
let scanning = false;
let lastAttempt = "";
let afterScanMessage = "";
let listedTree = null;
let view = "sun";
let viewRoot = null;
let activeOrganizePlan = null;
const zoomStack = [];

async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-File-Organiser-Token", sessionToken);
  try {
    return await fetch(url, { ...options, headers, credentials: "same-origin" });
  } catch {
    const error = "Could not reach File Organiser. Check that the local app is running.";
    statusEl.textContent = error;
    return new Response(JSON.stringify({ ok: false, error }), { status: 503 });
  }
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
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${unit ? n.toFixed(1) : n} ${units[unit]}`;
}

function showWorkspace() {
  document.querySelector("#welcome").hidden = true;
  document.querySelector("#workspace").hidden = false;
}

async function loadRoots() {
  const data = await apiFetch("/api/roots").then((r) => r.json());
  const rail = document.querySelector("#roots");
  rail.innerHTML = (data.roots || [])
    .map((r) => `<button type="button" data-path="${esc(r.path)}">${esc(fmt(r.size))}</button>`)
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
  inspect.size.textContent = fmt(node.size || 0);
  const detail = node.dir ? `Folder · ${(node.files || 0).toLocaleString()} files` : "File";
  inspect.meta.textContent = [detail, node.path, node.reason].filter(Boolean).join("\n");
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
  canvas.dataset.view = view;
  const width = view === "tree" ? 960 : 720;
  if (canvas.width !== width) {
    canvas.width = width;
    canvas.height = view === "tree" ? 500 : 720;
  }
  tree.label = fmt(tree.size);
  if (view === "tree") {
    treeHits = drawTreemap(canvas, tree, selected && selected.path);
    slices = [];
  } else {
    slices = drawSunburst(canvas, tree, selected && selected.path);
    treeHits = [];
  }
  byId("view-tree").setAttribute("aria-pressed", view === "tree");
  byId("view-sun").setAttribute("aria-pressed", view === "sun");
  byId("zoom-out").disabled = zoomStack.length === 0;
  if (listedTree !== tree) {
    listedTree = tree;
    renderMapContents(tree);
  }
  const bits = [scan.name, scan.visited + " files"];
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
    ? `<input type="checkbox" data-path="${esc(item.path)}" aria-label="Select ${esc(item.name)}" ${selectedPaths.has(item.path) ? "checked" : ""}>`
    : "";
  return `<div class="row">${box}<button type="button" data-inspect="${esc(item.path)}" title="${esc(item.path)}">
    ${esc(item.name)}<span class="why-line">${esc(item.reason)}</span></button><span class="sz">${esc(fmt(item.size))}</span></div>`;
}

function fillAdvice(payload) {
  if (!payload?.summary) return;
  const summary = payload.summary;
  byId("headline").textContent = summary.headline;
  let matches = 0;
  let total = 0;
  for (const bucket of ["delete", "review", "keep"]) {
    const all = payload[bucket];
    const rows = matchingAdvice(all);
    const list = byId(`list-${bucket}`);
    matches += rows.length;
    total += all.length;
    byId(`sum-${bucket}`).textContent = `${fmt(summary[bucket + "_bytes"])} · ${all.length} items`;
    list.innerHTML = rows.map((item) => adviceRow(item, bucket === "delete")).join("")
      || `<p class="meta">${all.length ? "No matches in this group." : "No items in this group."}</p>`;
    list.onclick = (event) => {
      const button = event.target.closest("button[data-inspect]");
      const item = button && rows.find((row) => row.path === button.dataset.inspect);
      if (item) inspectFromList({ ...item, safeDelete: bucket === "delete" });
    };
  }
  byId("match-count").textContent = `${matches} of ${total} suggestions`;
  updateSelection();
}

byId("advice-search").addEventListener("input", () => fillAdvice(advice));
byId("advice-sort").addEventListener("change", () => fillAdvice(advice));
byId("list-delete").addEventListener("change", (event) => {
  const box = event.target;
  if (box.checked) selectedPaths.add(box.dataset.path);
  else selectedPaths.delete(box.dataset.path);
  updateSelection();
});
byId("clear-selection").addEventListener("click", () => { selectedPaths.clear(); fillAdvice(advice); });

function fillTabs() {
  if (!scan) return;
  for (const name of ["largest", "stale"]) {
    const rows = scan[name] || [];
    const panel = byId("tab-" + name);
    panel.innerHTML = table(rows, [
      { label: "Name", html: true, render: (r) => `<button class="link-btn" type="button" data-file="${esc(r.path)}">${esc(r.name)}</button>` },
      { label: "Size", render: (r) => fmt(r.size) },
      { label: "Type", render: (r) => r.category },
    ]);
    panel.onclick = (event) => {
      const button = event.target.closest("button[data-file]");
      const item = button && rows.find((row) => row.path === button.dataset.file);
      if (item) inspectFromList(item);
    };
  }
  const maxCat = Math.max(1, ...(scan.categories || []).map((c) => c.size));
  document.querySelector("#tab-cats").innerHTML = table(scan.categories || [], [
    { label: "Category", render: (r) => r.category },
    { label: "Size", html: true, render: (r) => `<progress max="${maxCat}" value="${Number(r.size)}" aria-label="Relative category size"></progress>${esc(fmt(r.size))}` },
    { label: "Files", render: (r) => r.files },
  ]);
  const w = scan.waste || {};
  document.querySelector("#tab-reclaim").innerHTML = `
    <p class="meta">Caches plus large files untouched for a year. The Advice board above is the decision list — this is the raw reclaim estimate.</p>
    <p><strong>${esc(w.total_label || "0B")}</strong> likely reclaimable</p>
    <ul class="meta">
      <li>Build/cache folders: ${esc(w.caches_label || "0")} (${esc(w.cache_files || 0)} files)</li>
      <li>Stale large files: ${esc(w.stale_label || "0")}</li>
      <li>Empty folders: ${esc(w.empty || 0)}</li>
    </ul>`;
}

function setScanning(active) {
  scanning = active;
  byId("progress").hidden = !active;
  byId("cancel").hidden = !active;
  byId("cancel").disabled = !active;
  for (const control of document.querySelectorAll("#path-form input, #path-form button:not(#cancel), #roots button, .hero-actions button, #rescan")) {
    control.disabled = active;
  }
}

function scanFailure(message) {
  setScanning(false);
  statusEl.textContent = message;
  statusEl.classList.add("error");
  byId("retry").hidden = false;
  byId("welcome").hidden = Boolean(scan);
}

async function poll() {
  try {
    const st = await apiFetch("/api/status").then((r) => r.json());
    if (st.status === "running") {
      statusEl.textContent = `Scanning ${st.visited.toLocaleString()} files · ${st.label}`;
      setTimeout(poll, 350);
      return;
    }
    if (st.status !== "done") {
      scanFailure(st.status === "cancelled" ? "Scan stopped. Start a new scan to see complete results." : st.error || "The scan did not finish.");
      return;
    }
    const data = await apiFetch("/api/scan").then((r) => r.json());
    if (!data.result || !data.advice) { scanFailure(data.error || "Scan results are unavailable."); return; }
    scan = data.result;
    advice = data.advice;
    viewRoot = scan.tree;
    zoomStack.length = 0;
    pathInput.value = scan.root;
    setScanning(false);
    statusEl.textContent = `${afterScanMessage}Mapped ${scan.files.toLocaleString()} files in ${scan.elapsed_ms} ms.`;
    afterScanMessage = "";
    showWorkspace();
    renderOverview(scan, advice.summary);
    showInspect(scan.tree);
    paint();
    fillTabs();
    fillAdvice(advice);
    document.querySelector('[data-tab="largest"]').click();
  } catch {
    scanFailure("Scan results could not be loaded. Try again.");
  }
}

async function startScan(path, message = "") {
  if (!path || scanning) return;
  lastAttempt = path;
  byId("retry").hidden = true;
  statusEl.classList.remove("error");
  setScanning(true);
  byId("cancel").disabled = true;
  statusEl.textContent = "Starting scan…";
  try {
    const data = await apiFetch("/api/scan", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    }).then((r) => r.json());
    if (!data.ok) { scanFailure(data.error || "Could not start the scan."); return; }
    scan = advice = selected = activeOrganizePlan = null;
    selectedPaths.clear();
    byId("advice-search").value = "";
    for (const id of ["tab-dupes", "tab-plan", "tab-clutter"]) byId(id).replaceChildren();
    byId("workspace").hidden = true;
    byId("welcome").hidden = true;
    byId("cancel").disabled = false;
    afterScanMessage = message;
    pathInput.value = data.root;
    await poll();
  } catch {
    scanFailure("Could not start the scan. Check the local app and retry.");
  }
}

byId("rescan").addEventListener("click", () => startScan(scan?.root));
byId("retry").addEventListener("click", () => startScan(pathInput.value.trim() || lastAttempt));

async function zoomInto(node) {
  if (!node || !node.dir || !node.path) return;
  if (node.children && node.children.length) {
    zoomStack.push(viewRoot);
    viewRoot = node;
    showInspect(node);
    paint();
    return;
  }
  const previousRoot = viewRoot;
  const previousScan = scan;
  const data = await apiFetch("/api/zoom", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: node.path }),
  }).then((r) => r.json());
  if (!data.ok) {
    statusEl.textContent = data.error;
    return;
  }
  if (scan !== previousScan || previousRoot !== viewRoot) return;
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


document.querySelector("#cancel").addEventListener("click", async () => {
  byId("cancel").disabled = true;
  statusEl.textContent = "Stopping scan…";
  const result = await apiFetch("/api/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((r) => r.json());
  if (!result.ok) { statusEl.textContent = result.error; byId("cancel").disabled = false; }
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
  if (data.ok) startScan(scan?.root, statusEl.textContent + ". ");
});
document.querySelector("#why").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  const item = selected;
  const data = await apiFetch("/api/why?path=" + encodeURIComponent(item.path)).then((r) => r.json());
  if (item !== selected) return;
  inspect.why.hidden = false;
  inspect.why.textContent = (data.why && data.why.why ? data.why.why.join("\n") : data.error) || "";
});

document.querySelector("#trash-checked").addEventListener("click", async () => {
  const items = selectionItems();
  const reviewedScan = scan;
  if (!items.length || !await reviewTrash(items) || reviewedScan !== scan) return;
  byId("trash-checked").disabled = true;
  const paths = items.map((item) => item.path);
  const res = await apiFetch("/api/advise/purge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  }).then((r) => r.json());
  statusEl.textContent = res.ok ? `Trashed ${res.n} · skipped ${ (res.skipped || []).length }` : res.error;
  if (res.ok) startScan(reviewedScan.root, statusEl.textContent + ". ");
  else updateSelection();
});

document.querySelector("#tabs").addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.classList.toggle("on", b === btn);
    b.setAttribute("aria-pressed", b === btn);
  });
  document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("on", el.id === "tab-" + btn.dataset.tab));
});

document.querySelector('[data-tab="clutter"]').addEventListener("click", async () => {
  if (!scan) return;
  const box = document.querySelector("#tab-clutter");
  box.innerHTML = "<p class='meta'>Reading names…</p>";
  const auditedScan = scan;
  const data = await apiFetch("/api/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: scan.root, preview: true }),
  }).then((r) => r.json());
  if (scan !== auditedScan) return;
  if (!data.ok) {
    box.textContent = data.error || "Audit failed";
    return;
  }
  const audit = data.audit;
  const loose = (audit.loose || [])
    .map((item) => `<li>${esc(item.rel)} <span class="meta">${esc(item.category)}</span></li>`)
    .join("");
  const twins = (audit.twins || [])
    .map((group) => {
      const names = group.files.map((item) => esc(item.rel)).join(", ");
      return `<li>${esc(group.count)}× ${esc(group.stem + group.suffix)} — ${names}</li>`;
    })
    .join("");
  const clashes = (audit.conflicts || [])
    .map((clash) => `<li>${esc(clash.extension)} → ${esc(clash.categories.join(", "))}</li>`)
    .join("");
  const preview = (audit.preview || [])
    .map((item) => `<li>${esc(item.name)} → ${esc(item.rel_dest)}</li>`)
    .join("");
  box.innerHTML = `
    <p><strong>${esc(audit.score)}/100 ${esc(audit.grade)}</strong></p>
    <p class="meta">${esc(audit.headline)}</p>
    <p class="meta">${esc(audit.movable_count)} loose file(s) already match a category. The tidy list is a preview; this tab does not move files.</p>
    <p>Loose</p>
    <ul>${loose || "<li>None</li>"}</ul>
    <p>Copy-like names</p>
    <ul>${twins || "<li>None</li>"}</ul>
    <p>Rule overlaps</p>
    <ul>${clashes || "<li>None</li>"}</ul>
    <p>Tidy preview</p>
    <ul>${preview || "<li>None</li>"}</ul>
  `;
});

document.querySelector('[data-tab="dupes"]').addEventListener("click", openDuplicateReview);

function organizePanel() {
  return document.querySelector("#tab-plan");
}

async function refreshOrganizeStatus() {
  const host = document.querySelector("#organize-history");
  if (!host) return;
  const data = await apiFetch("/api/organize/status").then((r) => r.json());
  if (!data.ok) {
    host.textContent = data.error || "Could not read organise history.";
    return;
  }
  const undo = data.undo;
  if (!undo) {
    host.innerHTML = "<p class='meta'>No organise transaction is available to undo for this folder.</p>";
    return;
  }
  host.innerHTML = `<div class="transaction-card">
    <div><strong>Latest transaction</strong><p class="meta">${esc(undo.count)} files · ${esc(undo.timestamp)} · id ${esc(undo.transaction_id.slice(0, 10))}</p></div>
    <button class="btn ghost" id="undo-organize" type="button" ${undo.ready ? "" : "disabled"}>Undo organise</button>
    ${undo.ready ? "" : `<p class="transaction-error">${esc(undo.error || "Undo is not currently safe.")}</p>`}
  </div>`;
  const undoButton = host.querySelector("#undo-organize");
  if (!undoButton) return;
  undoButton.addEventListener("click", async () => {
    if (!confirm(`Undo transaction ${undo.transaction_id.slice(0, 10)} and restore ${undo.count} file(s) to their original paths?`)) return;
    undoButton.disabled = true;
    const result = await apiFetch("/api/organize/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_id: undo.transaction_id }),
    }).then((r) => r.json());
    statusEl.textContent = result.ok
      ? `Restored ${result.completed} files from transaction ${undo.transaction_id.slice(0, 10)}`
      : result.error || "Undo failed safely.";
    await refreshOrganizeStatus();
    if (result.ok) startScan(scan?.root, statusEl.textContent + ". ");
  });
}

function renderOrganizePlan(plan) {
  activeOrganizePlan = plan;
  const preview = document.querySelector("#organize-preview");
  if (!preview) return;
  const files = plan.files || [];
  const categoryText = Object.entries(plan.by_category || {})
    .map(([category, count]) => `${esc(category)} ${esc(count)}`)
    .join(" · ");
  preview.innerHTML = `<div class="transaction-card ready">
      <div>
        <strong>Verified plan ${esc(plan.id.slice(0, 10))}</strong>
        <p class="meta">${esc(plan.count)} files · ${esc(fmt(plan.total_bytes || 0))} · ${esc(plan.profile || "standard")} profile · SHA-256</p>
        <p class="meta">${categoryText || "No matching files."}</p>
      </div>
      <button class="btn" id="apply-plan" type="button" ${plan.count ? "" : "disabled"}>Apply this exact plan</button>
    </div><div id="move-pages"></div>`;
  renderMovePages(byId("move-pages"), files, scan.root);
  const applyButton = document.querySelector("#apply-plan");
  if (!applyButton) return;
  applyButton.addEventListener("click", async () => {
    const current = activeOrganizePlan;
    if (!current) return;
    if (!confirm(`Apply verified plan ${current.id.slice(0, 10)} and move ${current.count} file(s)?\nIf anything changed since preview, nothing will move.`)) return;
    applyButton.disabled = true;
    applyButton.textContent = "Verifying…";
    const result = await apiFetch("/api/organize/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_id: current.id }),
    }).then((r) => r.json());
    activeOrganizePlan = null;
    statusEl.textContent = result.ok
      ? `Moved ${result.completed} files · transaction ${result.transaction_id.slice(0, 10)}`
      : result.error || "The plan was not applied.";
    if (result.ok) {
      preview.innerHTML = "<p class='meta'>Transaction committed. The exact move can be undone below.</p>";
      await refreshOrganizeStatus();
      startScan(scan?.root, statusEl.textContent + ". ");
    } else {
      preview.innerHTML = `<p class="transaction-error">${esc(result.error || "Apply failed safely.")} Create a fresh plan before trying again.</p>`;
    }
  });
}

async function loadOrganizePanel() {
  const panel = organizePanel();
  if (!scan) {
    panel.innerHTML = "<p class='meta'>Scan a folder before organising it.</p>";
    return;
  }
  panel.innerHTML = `<div class="organize-controls">
      <label>Folder layout <select id="organize-profile"></select></label>
      <button class="btn" id="preview-organize" type="button">Create verified plan</button>
      <p class="meta">Planning hashes file contents. Apply commits only this exact preview and rolls the whole batch back if any move fails.</p>
    </div>
    <div id="organize-preview"><p class="meta">Choose a layout, then create a plan. Nothing moves during preview.</p></div>
    <div id="organize-history"></div>`;
  const select = panel.querySelector("#organize-profile");
  const previewButton = panel.querySelector("#preview-organize");
  previewButton.disabled = true;
  activeOrganizePlan = null;
  const profiles = await apiFetch("/api/profiles").then((r) => r.json());
  if (!select.isConnected) return;
  if (!profiles.ok) { panel.textContent = profiles.error; return; }
  previewButton.disabled = false;
  select.addEventListener("change", () => {
    activeOrganizePlan = null;
    byId("organize-preview").textContent = "Create a new plan to preview this layout.";
  });
  select.innerHTML = (profiles.profiles || [])
    .map((profile) => `<option value="${esc(profile.name)}">${esc(profile.name)} — ${esc(profile.description)}</option>`)
    .join("");
  if (scan.root && /(^|[\\/])Downloads$/i.test(scan.root)) select.value = "downloads";
  previewButton.addEventListener("click", async (event) => {
    select.disabled = true;
    activeOrganizePlan = null;
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Hashing…";
    document.querySelector("#organize-preview").innerHTML = "<p class='meta'>Building content fingerprints…</p>";
    const data = await apiFetch("/api/organize/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: scan.root,
        profile: select.value,
        recursive: false,
        magic: true,
        smart: true,
      }),
    }).then((r) => r.json());
    if (!button.isConnected) return;
    select.disabled = false;
    button.disabled = false;
    button.textContent = "Create verified plan";
    if (!data.ok) {
      document.querySelector("#organize-preview").innerHTML = `<p class="transaction-error">${esc(data.error || "Could not build plan.")}</p>`;
      return;
    }
    renderOrganizePlan(data.plan);
  });
  await refreshOrganizeStatus();
}

document.querySelector('[data-tab="plan"]').addEventListener("click", loadOrganizePanel);

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
  if (byId("trash-dialog").open || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
  if (event.key === "Backspace") {
    event.preventDefault();
    zoomOut();
  }
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
      lastAttempt = st.root;
      byId("welcome").hidden = true;
      setScanning(true);
      poll();
      return;
    }
  }
  const home = rootByLabel(roots, "Home folder");
  if (home && !pathInput.value) pathInput.value = home.path;
  if (st.status === "cancelled" || st.status === "error") scanFailure(st.error || "Previous scan stopped.");
});
