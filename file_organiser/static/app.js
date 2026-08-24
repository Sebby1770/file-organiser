const pathInput = document.querySelector("#path");
const statusEl = document.querySelector("#status");
const canvas = document.querySelector("#sun");
const inspect = {
  name: document.querySelector("#i-name"),
  size: document.querySelector("#i-size"),
  meta: document.querySelector("#i-meta"),
  actions: document.querySelector("#i-actions"),
  why: document.querySelector("#i-why"),
};
let scan = null;
let slices = [];
let selected = null;
let pollTimer = null;

function fmt(n) {
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " TB";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " KB";
  return n + " B";
}

async function loadRoots() {
  const data = await fetch("/api/roots").then((r) => r.json());
  const rail = document.querySelector("#roots");
  rail.innerHTML = (data.roots || [])
    .map((r) => `<button type="button" data-path="${r.path}">${r.label}</button>`)
    .join("");
  rail.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      pathInput.value = btn.dataset.path;
      startScan(btn.dataset.path);
    });
  });
}

function showInspect(node) {
  selected = node;
  inspect.name.textContent = node.name || node.path;
  inspect.size.textContent = node.label || fmt(node.size || 0);
  inspect.meta.textContent = `${node.dir ? "folder" : "file"} · ${node.files || 0} files\n${node.path || ""}`;
  inspect.actions.hidden = !node.path;
  inspect.why.hidden = true;
}

function paint() {
  if (!scan || !scan.tree) return;
  scan.tree.label = scan.label;
  slices = drawSunburst(canvas, scan.tree, selected && selected.path);
  document.querySelector("#crumb").textContent = scan.root + " · " + scan.visited + " files";
}

function table(rows, cols) {
  if (!rows.length) return "<p class='meta'>Nothing here.</p>";
  const head = "<tr>" + cols.map((c) => `<th>${c.label}</th>`).join("") + "</tr>";
  const body = rows
    .map((row) => {
      const tds = cols.map((c) => `<td>${c.render(row)}</td>`).join("");
      return `<tr data-path="${row.path || ""}">${tds}</tr>`;
    })
    .join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
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
    { label: "Size", render: (r) => `<span class="bar" style="width:${Math.max(8, (r.size / maxCat) * 180)}px"></span>${r.label}` },
    { label: "Files", render: (r) => r.files },
  ]);
  document.querySelectorAll("#tab-largest tr[data-path], #tab-stale tr[data-path]").forEach((tr) => {
    tr.addEventListener("click", () => {
      showInspect({ name: tr.children[0].textContent, path: tr.dataset.path, size: 0, label: tr.children[1].textContent, files: 1, dir: false });
    });
  });
}

async function poll() {
  const st = await fetch("/api/status").then((r) => r.json());
  if (st.status === "running") {
    statusEl.textContent = `Scanning… ${st.visited} files · ${st.label}`;
    return;
  }
  clearInterval(pollTimer);
  pollTimer = null;
  const data = await fetch("/api/scan").then((r) => r.json());
  if (data.error) {
    statusEl.textContent = data.error;
    return;
  }
  scan = data.result;
  statusEl.textContent = `Mapped ${scan.files} files · ${scan.label} in ${scan.elapsed_ms} ms`;
  showInspect(scan.tree);
  paint();
  fillTabs();
}

async function startScan(path) {
  statusEl.textContent = "Starting scan…";
  const res = await fetch("/api/scan", {
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
  pollTimer = setInterval(poll, 400);
  poll();
}

document.querySelector("#path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startScan(pathInput.value.trim());
});

canvas.addEventListener("click", (event) => {
  const node = hitSunburst(slices, canvas, event.clientX, event.clientY);
  if (node) {
    showInspect(node);
    paint();
  }
});

document.querySelector("#reveal").addEventListener("click", () => {
  if (!selected || !selected.path) return;
  fetch("/api/reveal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selected.path }) });
});

document.querySelector("#trash").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  if (!confirm(`Move to trash?\n${selected.path}`)) return;
  const data = await fetch("/api/trash", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: selected.path }),
  }).then((r) => r.json());
  statusEl.textContent = data.ok ? `${data.action} ${selected.name}` : data.error;
  if (data.ok) startScan(pathInput.value.trim());
});

document.querySelector("#why").addEventListener("click", async () => {
  if (!selected || !selected.path) return;
  const data = await fetch("/api/why?path=" + encodeURIComponent(selected.path)).then((r) => r.json());
  inspect.why.hidden = false;
  inspect.why.textContent = (data.why && data.why.why ? data.why.why.join("\n") : data.error) || "";
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
  const data = await fetch("/api/dupes", {
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
    `<p class="meta">Reclaimable ${data.reclaim_label} across ${data.extra_files} extra copies.</p>` +
    groups
      .map((g) => `<p><strong>${g.count}×</strong> keep ${g.keep}<br>${g.files.map((f) => f.name).join(" · ")}</p>`)
      .join("");
  document.querySelector("#tab-dupes").innerHTML = html || "<p class='meta'>No duplicates over 256 KB.</p>";
});

document.querySelector('[data-tab="plan"]').addEventListener("click", async () => {
  if (!scan) return;
  document.querySelector("#tab-plan").innerHTML = "<p class='meta'>Planning…</p>";
  const data = await fetch("/api/organize", {
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
    `<p class="meta">${data.plan.count} files would move. Dry-run only — run the CLI to apply.</p>` +
    table(
      files.slice(0, 40).map((f) => ({ name: f.source.split("/").pop(), path: f.source, category: f.category, dest: f.destination })),
      [
        { label: "File", render: (r) => r.name },
        { label: "Category", render: (r) => r.category },
      ]
    );
});

loadRoots().then(async () => {
  const st = await fetch("/api/status").then((r) => r.json());
  if (st.root) {
    pathInput.value = st.root;
    if (st.status === "done" || st.status === "running") {
      if (!pollTimer) pollTimer = setInterval(poll, 400);
      poll();
      return;
    }
  }
  const first = document.querySelector("#roots button");
  if (first && !pathInput.value) pathInput.value = first.dataset.path;
});
