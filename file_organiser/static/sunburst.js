const PALETTE = ["#d4a054", "#7eb8d4", "#c47aff", "#6ee7b7", "#e07a6a", "#f0c36d", "#8ab4f8", "#f6a6ff", "#9ad0b4", "#ffb38a"];

function colorFor(name, i) {
  let h = 0;
  for (const ch of String(name)) h = (h * 33 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[(h + i) % PALETTE.length];
}

function layoutSunburst(tree) {
  const slices = [];
  const total = Math.max(1, tree.size || 1);
  function walk(node, depth, start, span, idx) {
    if (!node || span <= 0) return;
    slices.push({
      node,
      depth,
      start,
      span,
      color: node.other ? "#3a4150" : colorFor(node.name, idx),
    });
    const kids = node.children || [];
    let a = start;
    kids.forEach((kid, i) => {
      const childSpan = span * (kid.size / Math.max(1, node.size || 1));
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
  const ring = maxR / 5;
  slices.forEach((s) => {
    const inner = s.depth * ring;
    const outer = inner + ring - 2;
    ctx.beginPath();
    ctx.arc(cx, cy, outer, s.start, s.start + s.span);
    ctx.arc(cx, cy, inner, s.start + s.span, s.start, true);
    ctx.closePath();
    ctx.fillStyle = s.color;
    ctx.globalAlpha = highlightPath && s.node.path === highlightPath ? 1 : 0.88;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#0c0e12";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
  ctx.fillStyle = "#e8edf4";
  ctx.font = "600 18px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(tree.name || "", cx, cy - 6);
  ctx.fillStyle = "#8b95a8";
  ctx.font = "13px ui-monospace, monospace";
  ctx.fillText(tree.label || "", cx, cy + 16);
  return slices;
}

function hitSunburst(slices, canvas, x, y) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const px = (x - rect.left) * scaleX;
  const py = (y - rect.top) * scaleY;
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const dx = px - cx;
  const dy = py - cy;
  const r = Math.hypot(dx, dy);
  let ang = Math.atan2(dy, dx);
  const ring = (Math.min(cx, cy) - 12) / 5;
  const depth = Math.floor(r / ring);
  if (depth < 0) return null;
  const found = slices.filter((s) => s.depth === depth);
  for (const s of found) {
    let a0 = s.start;
    let a1 = s.start + s.span;
    let a = ang;
    while (a < a0) a += Math.PI * 2;
    while (a > a0 + Math.PI * 2) a -= Math.PI * 2;
    if (a >= a0 && a <= a1) return s.node;
  }
  return null;
}
