// The comparisons stay in this panel until the next folder scan or search.
function openDuplicateReview() {
  const panel = byId("tab-dupes");
  if (!scan || panel.childElementCount) return;
  const reviewedScan = scan;
  panel.innerHTML = `<form class="duplicate-search">
    <label>Minimum file size <select name="minimum">
      <option value="1">1 byte</option><option value="262144" selected>256 KiB</option>
      <option value="1048576">1 MiB</option><option value="10485760">10 MiB</option>
      <option value="104857600">100 MiB</option></select></label>
    <button type="submit" class="btn">Find duplicates</button>
  </form>
  <p class="meta" role="status">Compare non-empty files by content. Only copies on the Delete list can move to Trash.</p>
  <div class="duplicate-results"></div>`;
  const form = panel.querySelector("form");
  const status = panel.querySelector('[role="status"]');
  const results = panel.querySelector(".duplicate-results");
  const minimum = form.elements.minimum;
  const submit = form.querySelector("button");
  minimum.onchange = () => {
    results.replaceChildren();
    status.textContent = "Find duplicates to search with this minimum size.";
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    submit.disabled = minimum.disabled = true;
    results.replaceChildren();
    status.textContent = "Comparing file contents…";
    try {
      const data = await apiFetch("/api/dupes", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: reviewedScan.root, min_size: Number(minimum.value) }),
      }).then((res) => res.json());
      if (!form.isConnected || reviewedScan !== scan) return;
      if (!data.ok) { status.textContent = data.error; return; }
      status.textContent = data.groups.length
        ? `${data.groups.length} groups · ${data.extra_files} extra copies · ${fmt(data.reclaim)} of duplicate data. Largest groups first.`
        : `No duplicate copies found at or above ${minimum.selectedOptions[0].textContent}.`;
      if (data.skipped) status.textContent += ` ${data.skipped} matching paths changed or became unreadable before review.`;
      if (data.groups.length) showDuplicateGroups(results, data.groups, reviewedScan);
    } catch {
      status.textContent = "Could not load the comparison. Try the search again.";
    } finally {
      submit.disabled = minimum.disabled = false;
    }
  };
}

function showDuplicateGroups(host, groups, reviewedScan) {
  host.innerHTML = `<label class="duplicate-filter">Filter groups by path <input type="search" placeholder="Folder or filename"></label>
    <div class="pager"><button type="button" class="btn ghost" data-page="back">Previous group</button>
    <span role="status"></span><button type="button" class="btn ghost" data-page="next">Next group</button></div>
    <div class="duplicate-group"></div>`;
  const filter = host.querySelector("input");
  const current = host.querySelector(".duplicate-group");
  const previous = host.querySelector('[data-page="back"]');
  const next = host.querySelector('[data-page="next"]');
  const choices = new Map();
  let matched = groups;
  let page = 0;

  function render() {
    previous.disabled = page === 0;
    next.disabled = page + 1 >= matched.length;
    host.querySelector('[role="status"]').textContent = matched.length ? `Group ${page + 1} of ${matched.length}` : "No matching groups";
    current.replaceChildren();
    const group = matched[page];
    if (!group) return;
    if (!choices.has(group.group)) choices.set(group.group, { keep: group.keep, selected: new Set() });
    renderDuplicateGroup(current, group, choices.get(group.group), reviewedScan);
  }
  filter.oninput = () => {
    const text = filter.value.trim().toLocaleLowerCase();
    matched = groups.filter((group) => group.files.some((f) => f.relative.toLocaleLowerCase().includes(text)));
    page = 0;
    render();
  };
  previous.onclick = () => { page--; render(); };
  next.onclick = () => { page++; render(); };
  render();
}

function renderDuplicateGroup(host, group, choice, reviewedScan) {
  host.innerHTML = `<h3>${esc(group.files[0].name)}</h3>
    <p class="meta">${group.copies} distinct copies · ${fmt(group.size)} each. Choose a keeper, then select the extras to move.</p>
    <table class="duplicate-files"><thead><tr><th>File in this folder</th><th>Keep</th><th>Trash</th></tr></thead><tbody></tbody></table>
    <div class="duplicate-actions"><button class="btn danger" type="button" disabled>Review selected copies</button>
    <span role="status" class="meta"></span></div>`;
  const body = host.querySelector("tbody");
  const review = host.querySelector(".duplicate-actions button");
  const status = host.querySelector('[role="status"]');
  const keepers = [];
  const checks = [];
  group.files.forEach((file, index) => {
    const row = body.insertRow();
    row.innerHTML = `<td><button type="button" class="link-btn" title="${esc(file.path)}">${esc(file.relative)}</button>
      <small>${file.eligible ? "On the Delete list" : esc(file.reason === "Hard-linked file" ? file.reason : "Review only")}</small></td>
      <td><input type="radio" name="duplicate-keeper" aria-label="Keep ${esc(file.relative)}"></td>
      <td><input type="checkbox" aria-label="Select ${esc(file.relative)} for Trash"></td>`;
    const [keeper, check] = row.querySelectorAll("input");
    keepers[index] = keeper;
    checks[index] = check;
    keeper.onchange = () => { choice.keep = file.path; choice.selected.delete(file.path); update(); };
    check.onchange = () => {
      if (check.checked) choice.selected.add(file.path);
      else choice.selected.delete(file.path);
      update();
    };
    row.querySelector("button").onclick = () => inspectFromList(file);
  });
  function update() {
    group.files.forEach((file, index) => {
      keepers[index].checked = file.path === choice.keep;
      checks[index].checked = choice.selected.has(file.path);
      checks[index].disabled = !file.eligible || file.path === choice.keep;
    });
    review.disabled = !choice.selected.size;
    status.textContent = `${choice.selected.size} selected · ${fmt(group.size * choice.selected.size)}`;
  }
  review.onclick = async () => {
    const items = group.files.filter((f) => choice.selected.has(f.path));
    const keep = choice.keep;
    if (!items.length || !await reviewTrash(items, keep) || scan !== reviewedScan) return;
    review.disabled = true;
    status.textContent = "Rechecking file contents…";
    try {
      const data = await apiFetch("/api/dupes/purge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group: group.group, keep, paths: items.map((f) => f.path) }),
      }).then((res) => res.json());
      if (!data.ok) { status.textContent = data.error; return; }
      const skipped = data.skipped || [];
      const message = `Moved ${data.n} ${data.n === 1 ? "copy" : "copies"} to Trash.` + (skipped.length ? ` Skipped ${skipped.length}. ${skipped[0].error}` : "");
      if (reviewedScan === scan) await startScan(reviewedScan.root, message + " ");
    } catch {
      status.textContent = "Could not confirm the result. Scan the folder again.";
    }
  };
  update();
}
