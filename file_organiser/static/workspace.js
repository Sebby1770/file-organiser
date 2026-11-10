// Views shared by the scan overview, the map and the cleanup board.
const byId = (id) => document.getElementById(id);
const selectedPaths = new Set();

function renderOverview(result, summary) {
  byId("folder-name").textContent = result.name;
  byId("folder-path").textContent = result.root;
  byId("metric-size").textContent = fmt(result.size);
  byId("metric-files").textContent = `${result.files.toLocaleString()} files scanned`;
  byId("metric-delete").textContent = fmt(summary.delete_bytes);
  byId("metric-delete-count").textContent = `${summary.delete_n} suggested items`;
  byId("metric-review").textContent = summary.review_n;
  byId("metric-review-size").textContent = `${fmt(summary.review_bytes)} listed for review`;
  byId("metric-free").textContent = Number.isFinite(result.volume.free) ? fmt(result.volume.free) : "Unavailable";
  byId("metric-volume").textContent = result.volume.total_label ? `of ${fmt(result.volume.total)} on this volume` : "Volume capacity unavailable";
  byId("coverage").hidden = !result.errors;
  byId("coverage").textContent = `${result.errors} locations or files could not be read. Totals cover readable items only.`;
  byId("roots").querySelectorAll("button").forEach((btn) => btn.classList.toggle("on", btn.dataset.path === result.root));
}

function renderMapContents(tree) {
  const box = byId("map-contents");
  const nodes = tree.children || [];
  box.innerHTML = nodes.map((node, index) => `<div class="content-row">
    <button class="link-btn" type="button" data-inspect="${index}">${esc(node.name)}</button>
    <span>${esc(fmt(node.size))}</span>
    ${node.dir && node.path ? `<button class="link-btn" type="button" data-open="${index}" aria-label="Open ${esc(node.name)}">Open</button>` : ""}</div>`).join("") || "<p class='meta'>No visible entries.</p>";
  box.onclick = (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.open !== undefined) zoomInto(nodes[Number(button.dataset.open)]);
    else inspectFromList(nodes[Number(button.dataset.inspect)]);
  };
}

function inspectFromList(item) {
  showInspect(item);
  paint();
  byId("inspect").focus();
}

function matchingAdvice(rows) {
  const query = byId("advice-search").value.trim().toLocaleLowerCase();
  const sort = byId("advice-sort").value;
  const byName = (a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path);
  return rows.filter((row) => [row.name, row.path, row.reason].some((v) => String(v).toLocaleLowerCase().includes(query)))
    .sort((a, b) => sort === "name" ? byName(a, b) : (sort === "size-desc" ? b.size - a.size : a.size - b.size) || byName(a, b));
}

function selectionItems() {
  return (advice?.delete || []).filter((item) => selectedPaths.has(item.path));
}

function updateSelection() {
  const items = selectionItems();
  byId("trash-checked").disabled = !items.length;
  byId("clear-selection").disabled = !items.length;
  const directories = items.filter((item) => item.dir);
  const covered = (item) => directories.some((dir) => item.path.startsWith(dir.path + scan.separator));
  const bytes = items.reduce((sum, item) => sum + (covered(item) ? 0 : item.size), 0);
  byId("selection-count").textContent = items.length
    ? `${items.length} selected · ${fmt(bytes)}`
    : "Nothing selected";
}

function reviewTrash(items) {
  const dialog = byId("trash-dialog");
  byId("trash-summary").textContent = `Review all ${items.length} selected items before moving them to the operating system Trash.`;
  byId("trash-paths").replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    li.textContent = item.path;
    return li;
  }));
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}

function renderMovePages(element, files, root) {
  const pageSize = 40;
  let page = 0;
  function render() {
    const start = page * pageSize;
    const rows = files.slice(start, start + pageSize);
    const relative = (path) => path.startsWith(root) ? path.slice(root.length).replace(/^[\\/]/, "") : path;
    element.innerHTML = table(rows, [
      { label: "Current location", render: (file) => relative(file.source) },
      { label: "Destination", render: (file) => relative(file.destination) },
    ]);
    if (files.length <= pageSize) return;
    element.insertAdjacentHTML("beforeend", `<div class="pager"><button type="button" class="btn ghost" data-step="-1" ${page === 0 ? "disabled" : ""}>Previous</button>
      <span>${start + 1}–${start + rows.length} of ${files.length} moves</span>
      <button type="button" class="btn ghost" data-step="1" ${start + rows.length >= files.length ? "disabled" : ""}>Next</button></div>`);
  }
  element.onclick = (event) => {
    const button = event.target.closest("button[data-step]");
    if (button) { page += Number(button.dataset.step); render(); }
  };
  render();
}
