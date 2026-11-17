'use strict';
(() => {
  const id = name => document.getElementById(name);
  const demo = document.body.dataset.mode === 'demo';
  const schema = 'file-organiser.storage-snapshot.v1';
  const gib = 1024 ** 3;
  const types = {
    Images: 'jpg jpeg png gif bmp svg webp tif tiff ico heic heif raw cr2 cr3 nef arw dng avif',
    Videos: 'mp4 mov avi mkv flv wmv webm m4v braw r3d mxf mts',
    Audio: 'mp3 wav flac aac ogg m4a wma aiff',
    Documents: 'pdf doc docx txt rtf odt md tex pages', Ebooks: 'epub mobi azw azw3 fb2',
    Spreadsheets: 'xls xlsx csv ods tsv', Presentations: 'ppt pptx odp key',
    Archives: 'zip tar gz rar 7z bz2 xz iso img', Design: 'psd ai fig sketch xd indd afdesign',
    Models: 'obj fbx stl glb gltf blend', Code: 'py js ts jsx tsx java c cpp h hpp cs go rs rb php swift kt sh bash html css scss json xml yml yaml',
    Executables: 'exe msi dmg deb rpm appimage', Fonts: 'ttf otf woff woff2', Other: '',
  };
  const suffixes = new Map(Object.entries(types).flatMap(([type, list]) => list.split(' ').filter(Boolean).map(ext => [ext, type])));
  let current = null;
  let baseline = null;
  let generation = 0;
  let busy = false;

  function size(bytes) {
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
    let n = Math.abs(bytes), unit = 0;
    while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
    return `${Number(n.toFixed(unit ? 2 : 0)).toLocaleString()} ${units[unit]}`;
  }
  const signed = n => n > 0 ? `+${size(n)}` : n < 0 ? `−${size(n)}` : '0 B';
  const date = stamp => new Date(stamp).toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'});
  const integer = n => Number.isSafeInteger(n) && n >= 0;
  const totals = snap => snap.groups.reduce((sum, row) => ({bytes: sum.bytes + row.bytes, files: sum.files + row.files}), {bytes: 0, files: 0});

  function parseSnapshot(value) {
    const bad = () => { throw new Error('Choose a Storage Review Kit snapshot JSON. Desktop users can create one with “Export storage snapshot”.'); };
    if (!value || value.schema !== schema || !['browser', 'desktop'].includes(value.source) || !integer(value.errors) || !Array.isArray(value.groups) || value.groups.length > Object.keys(types).length) bad();
    if (typeof value.captured !== 'string' || !/^\d{4}-\d\d-\d\dT/.test(value.captured) || !Number.isFinite(Date.parse(value.captured))) bad();
    const seen = new Set();
    const groups = value.groups.map(row => {
      if (!row || typeof row.type !== 'string' || !Object.hasOwn(types, row.type) || seen.has(row.type) || !integer(row.bytes) || !integer(row.files) || (!row.files && row.bytes)) bad();
      seen.add(row.type);
      return {type: row.type, bytes: row.bytes, files: row.files};
    });
    const snap = {schema, source: value.source, captured: new Date(value.captured).toISOString(), errors: value.errors, groups};
    const sum = totals(snap);
    if (!integer(sum.bytes) || !integer(sum.files)) bad();
    return snap;
  }

  function checkPair(now, before) {
    if (!before) return;
    if (now.source !== before.source) throw new Error('Use two desktop snapshots or two browser snapshots so the scan rules match.');
    if (Date.parse(before.captured) > Date.parse(now.captured)) throw new Error('The baseline is newer than the current snapshot. Choose the earlier scan as your baseline.');
  }

  function render() {
    const available = !!current;
    const valid = id('budget').validity.valid;
    const budget = id('budget').valueAsNumber * gib;
    id('print').disabled = id('download').disabled = !available || !valid || busy;
    id('snapshot').disabled = !available || busy;
    id('empty').hidden = available;
    id('report-body').hidden = !available;
    id('report-kind').textContent = demo ? 'Sample storage review · fictional data' : 'Project storage review';
    id('report-name').textContent = id('project').value.trim() || 'Project handoff';
    id('byline').textContent = id('studio').value.trim() ? `Prepared by ${id('studio').value.trim()}` : '';
    id('date').textContent = available ? `Scan date · ${date(current.captured)}` : '';
    if (!demo) id('remove-baseline').disabled = !baseline || busy;
    if (!available) return;
    const now = totals(current);
    id('total').textContent = size(now.bytes);
    id('count').textContent = `${now.files.toLocaleString()} files`;
    id('delta').textContent = baseline ? signed(now.bytes - totals(baseline).bytes) : '—';
    id('baseline-date').textContent = baseline ? `Compared with ${date(baseline.captured)}` : 'No baseline selected';
    id('headroom').textContent = valid && Number.isFinite(budget) ? size(budget - now.bytes) : '—';
    id('budget-caption').textContent = !valid ? 'Enter a valid budget in GiB' : !Number.isFinite(budget) ? 'No budget set' : `${budget >= now.bytes ? 'Remaining in' : 'Over'} ${size(budget)} budget`;

    const rows = new Map();
    for (const row of current.groups) rows.set(row.type, {...row, change: row.bytes});
    for (const prev of baseline?.groups || []) {
      const row = rows.get(prev.type) || {type: prev.type, files: 0, bytes: 0, change: 0};
      row.change -= prev.bytes;
      rows.set(row.type, row);
    }
    const sorted = [...rows.values()].sort((a,b) => b.bytes - a.bytes || a.type.localeCompare(b.type));
    const biggestChange = [...sorted].sort((a,b) => Math.abs(b.change) - Math.abs(a.change))[0];
    id('finding').textContent = baseline && biggestChange?.change ? `${biggestChange.type} changed the most, ${biggestChange.change > 0 ? 'up' : 'down'} ${size(biggestChange.change)}.`
      : baseline ? 'File type sizes are unchanged across these snapshots.' : now.bytes ? `${sorted[0].type} accounts for ${Math.round(100 * sorted[0].bytes / now.bytes)}% of the current size.` : 'No storage bytes were counted.';
    id('coverage').hidden = !current.errors && !baseline?.errors;
    id('coverage').textContent = `Partial coverage. Unreadable items: ${current.errors} in the current scan${baseline ? ` and ${baseline.errors} in the baseline` : ''}. Differences may reflect access changes.`;
    id('breakdown').replaceChildren(...sorted.map(row => {
      const tr = document.createElement('tr');
      for (const text of [row.type, size(row.bytes), baseline ? signed(row.change) : '—', row.files.toLocaleString()]) tr.insertCell().textContent = text;
      const bar = document.createElement('meter');
      bar.max = Math.max(now.bytes, 1); bar.value = row.bytes; bar.setAttribute('aria-label', `${row.type} share of total`);
      tr.cells[0].append(bar);
      return tr;
    }));
    id('scope').textContent = 'Figures use logical file sizes. A smaller folder may reflect files moved elsewhere. Disk space freed, backup completeness, and file contents are not measured.';
  }

  function sample() {
    const film = id('sample').value === 'film';
    const snap = (stamp, rows) => ({schema, source:'sample', captured: stamp, errors:0, groups: rows.map(([type, gb, files]) => ({type, bytes: gb * gib, files}))});
    baseline = snap('2026-04-01T12:00:00Z', film ? [['Videos',40,24],['Audio',4,32],['Design',2,7]] : [['Images',80,1250],['Archives',8,3]]);
    current = snap('2026-04-08T12:00:00Z', film ? [['Videos',60,31],['Audio',5,38],['Design',1,4]] : [['Images',50,780],['Archives',12,4]]);
    id('project').value = film ? 'Short film delivery' : 'Spring photo archive';
    id('budget').value = film ? '64' : '80';
    render();
  }

  function reset() {
    generation++; busy = false; current = baseline = null;
    for (const name of ['project','studio','budget']) id(name).value = '';
    id('status').textContent = '';
    if (demo) { id('sample').value = 'film'; sample(); } else render();
  }

  async function load(input, target) {
    const files = input.files;
    if (!files.length) return;
    const token = ++generation;
    const isFolder = input.id === 'folder';
    const file = files[0];
    busy = true; render();
    id('status').textContent = isFolder ? 'Reading folder sizes…' : 'Reading snapshot…';
    try {
      let snap;
      if (isFolder) {
        const groups = new Map();
        for (let i = 0; i < files.length; i++) {
          if (i && i % 1000 === 0) {
            await new Promise(resolve => setTimeout(resolve, 0));
            if (token !== generation) return;
          }
          const f = files[i];
          if (f.webkitRelativePath.split('/').slice(1).some(part => part.startsWith('.'))) continue;
          const dot = f.name.lastIndexOf('.');
          const type = (dot > 0 && suffixes.get(f.name.slice(dot + 1).toLowerCase())) || 'Other';
          const row = groups.get(type) || {type, files: 0, bytes: 0};
          row.files++; row.bytes += f.size; groups.set(type, row);
        }
        snap = parseSnapshot({schema, source:'browser', captured:new Date().toISOString(), errors:0, groups:[...groups.values()]});
      } else {
        if (file.size > 65536) throw new Error('Snapshot is too large. Choose an aggregate storage snapshot under 64 KiB.');
        snap = parseSnapshot(JSON.parse(await file.text()));
      }
      if (token !== generation) return;
      if (target === 'baseline') {
        if (!current) throw new Error('Load a current snapshot first.');
        checkPair(current, snap); baseline = snap;
      } else {
        checkPair(snap, baseline); current = snap;
      }
      id('status').textContent = target === 'baseline' ? 'Comparison ready. Check that both snapshots describe the same project.' : 'Current snapshot loaded.';
    } catch (error) {
      if (token === generation) id('status').textContent = error instanceof SyntaxError ? 'That file is not valid snapshot JSON.' : error.message;
    } finally {
      if (token === generation) { input.value = ''; busy = false; render(); }
    }
  }

  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], {type}));
    const link = document.createElement('a'); link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  for (const name of ['project','studio','budget']) id(name).oninput = render;
  id('reset').onclick = reset;
  id('print').onclick = () => window.print();
  id('download').onclick = () => {
    const report = id('report').cloneNode(true);
    report.querySelectorAll('[hidden]').forEach(node => node.remove());
    const css = 'body{max-width:900px;margin:40px auto;padding:0 18px;color:#25382c;font:16px/1.5 system-ui,sans-serif}h2{font:38px Georgia,serif}.kicker,small,th,.footnote{color:#4b6256}.metrics{display:flex;flex-wrap:wrap;gap:24px;border-block:1px solid #bac9b5;padding:15px 0}.metrics strong,.metrics small{display:block}.metrics strong{font-size:24px}table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child,caption{text-align:left}meter{display:block}article{overflow-wrap:anywhere}';
    download('storage-review.html', `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Storage review</title><style>${css}</style><main>${report.outerHTML}</main></html>`, 'text/html');
  };
  if (demo) {
    id('sample').onchange = sample;
    const offer = id('offer');
    try {
      const url = new URL(window.STORAGE_KIT_CHECKOUT);
      if (url.protocol === 'https:' && !url.username && !url.password) {
        offer.href = url.href; offer.textContent = 'Buy the offline kit ↗'; id('offer-note').textContent = 'Price and delivery details at checkout';
      }
    } catch { /* Unconfigured stores use the existing early-access form. */ }
  } else {
    id('inputs').hidden = false; id('samples').hidden = true; id('snapshot').hidden = false;
    for (const name of ['folder','current-file','baseline-file']) id(name).onchange = () => load(id(name), name === 'baseline-file' ? 'baseline' : 'current');
    id('remove-baseline').onclick = () => { baseline = null; id('status').textContent = 'Baseline cleared.'; render(); };
    id('snapshot').onclick = () => download('storage-snapshot.json', JSON.stringify(current, null, 2), 'application/json');
  }
  reset();
})();
