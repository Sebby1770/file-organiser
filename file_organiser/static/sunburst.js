const PALETTE = ["#e4ab7e", "#93b5bd", "#b3c69a", "#84b5a6", "#d5927e", "#e0c488", "#9baec6", "#c1aac2", "#b3c2a3", "#e3b18d"];
const CAT_COLOR = {
  Images: "#e8a54b",
  Documents: "#7eb8d4",
  Videos: "#c47aff",
  Audio: "#6ee7b7",
  Archives: "#f0c36d",
  Code: "#8ab4f8",
  Ebooks: "#f6a6ff",
  Design: "#ffb38a",
  Models: "#9ad0b4",
  Other: "#6b7280",
};

function colorFor(name, i, category) {
  if (category && CAT_COLOR[category]) return CAT_COLOR[category];
  let h = 0;
  for (const ch of String(name)) h = (h * 33 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[(h + i) % PALETTE.length];
}

function layoutSunburst(tree) {
  const slices = [];
  function walk(node, depth, start, span, idx) {
    if (!node || span <= 0.0001) return;
    slices.push({
      node,
      depth,
      start,
      span,
      color: node.other ? "#3a4150" : colorFor(node.name, idx, node.category),
    });
    const kids = node.children || [];
    let a = start;
    const parentSize = Math.max(1, node.size || 1, kids.reduce((sum, kid) => sum + kid.size, 0));
    kids.forEach((kid, i) => {
      const childSpan = span * ((kid.size || 0) / parentSize);
      walk(kid, depth + 1, a, childSpan, i);
      a += childSpan;
    });
  }
  walk(tree, 0, -Math.PI / 2, Math.PI * 2, 0);
  return slices;
}

function drawSunburst(canvas, tree, highlightPath) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const maxR = Math.min(cx, cy) - 12;
  ctx.clearRect(0, 0, w, h);
  if (!tree) return [];
  const slices = layoutSunburst(tree);
  const maxDepth = Math.max(1, ...slices.map((s) => s.depth));
  const ring = maxR / (maxDepth + 1.2);
  slices.forEach((s) => {
    const inner = s.depth === 0 ? 0 : s.depth * ring;
    const outer = s.depth === 0 ? ring * 0.55 : inner + ring - 2;
    ctx.beginPath();
    ctx.arc(cx, cy, outer, s.start, s.start + s.span);
    ctx.arc(cx, cy, inner, s.start + s.span, s.start, true);
    ctx.closePath();
    ctx.fillStyle = s.color;
    ctx.globalAlpha = highlightPath && s.node.path === highlightPath ? 1 : s.depth === 0 ? 0.35 : 0.9;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#0c0e12";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
  ctx.fillStyle = "#e8edf4";
  ctx.font = "600 16px system-ui";
  ctx.textAlign = "center";
  ctx.fillText((tree.name || "").slice(0, 22), cx, cy - 6);
  ctx.fillStyle = "#8b95a8";
  ctx.font = "13px ui-monospace, monospace";
  ctx.fillText(tree.label || "", cx, cy + 16);
  return slices;
}

function drawTreemap(canvas, tree, highlightPath) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!tree) return [];
  const hits = [];
  const nodes = (tree.children?.length ? tree.children : [tree])
    .filter((node) => node.size > 0)
    .sort((a, b) => b.size - a.size);

  // Split near half the byte total along the longest side. Each leaf keeps
  // its exact area share while small siblings get usable, two-dimensional tiles.
  function tile(group, x, y, w, h) {
    if (!group.length) return;
    if (group.length === 1) {
      hits.push({ node: group[0], x, y, w, h });
      return;
    }
    const total = group.reduce((sum, node) => sum + node.size, 0);
    let cut = 1;
    let weight = group[0].size;
    while (cut < group.length - 1 && Math.abs(total / 2 - weight - group[cut].size) < Math.abs(total / 2 - weight)) {
      weight += group[cut++].size;
    }
    const share = weight / total;
    if (w >= h) {
      tile(group.slice(0, cut), x, y, w * share, h);
      tile(group.slice(cut), x + w * share, y, w * (1 - share), h);
    } else {
      tile(group.slice(0, cut), x, y, w, h * share);
      tile(group.slice(cut), x, y + h * share, w, h * (1 - share));
    }
  }
  tile(nodes, 8, 8, canvas.width - 16, canvas.height - 16);
  hits.forEach(({ node, x, y, w, h }, index) => {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    ctx.textAlign = "start";
    ctx.fillStyle = node.other ? "#9da69a" : colorFor(node.name, index, node.category);
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = highlightPath && highlightPath === node.path ? "#fff" : "#25352b";
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, w, h);
    if (w > 64 && h > 38) {
      ctx.fillStyle = "#172018";
      ctx.font = "600 20px system-ui";
      ctx.fillText(node.name, x + 8, y + 25);
      if (h > 65) {
        ctx.font = "16px system-ui";
        ctx.fillText(fmt(node.size), x + 8, y + 49);
      }
    }
    ctx.restore();
  });
  if (!nodes.length) {
    ctx.fillStyle = "#d9e4d4";
    ctx.font = "20px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("No file data to map", canvas.width / 2, canvas.height / 2);
    ctx.textAlign = "start";
  }
  return hits;
}

function hitSunburst(slices, canvas, x, y) {
  const rect = canvas.getBoundingClientRect();
  const px = (x - rect.left) * (canvas.width / rect.width);
  const py = (y - rect.top) * (canvas.height / rect.height);
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const dx = px - cx;
  const dy = py - cy;
  const r = Math.hypot(dx, dy);
  let ang = Math.atan2(dy, dx);
  const maxDepth = Math.max(1, ...slices.map((s) => s.depth));
  const ring = (Math.min(cx, cy) - 12) / (maxDepth + 1.2);
  const depth = Math.floor(r / ring);
  if (depth < 0) return null;
  for (const s of slices) {
    if (s.depth !== depth) continue;
    let a = ang;
    const a0 = s.start;
    const a1 = s.start + s.span;
    while (a < a0) a += Math.PI * 2;
    while (a > a0 + Math.PI * 2) a -= Math.PI * 2;
    if (a >= a0 && a <= a1) return s.node;
  }
  return null;
}

function hitTreemap(hits, canvas, x, y) {
  const rect = canvas.getBoundingClientRect();
  const px = (x - rect.left) * (canvas.width / rect.width);
  const py = (y - rect.top) * (canvas.height / rect.height);
  for (const h of hits) {
    if (px >= h.x && px <= h.x + h.w && py >= h.y && py <= h.y + h.h) return h.node;
  }
  return null;
}
