const RULES = {
  Images: [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".svg", ".ico", ".bmp", ".tiff", ".raw", ".cr2", ".nef"],
  Documents: [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".tex", ".pages"],
  Ebooks: [".epub", ".mobi", ".azw", ".azw3", ".fb2"],
  Spreadsheets: [".xls", ".xlsx", ".csv", ".ods", ".tsv"],
  Presentations: [".ppt", ".pptx", ".odp", ".key"],
  Videos: [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv"],
  Audio: [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"],
  Archives: [".zip", ".tar", ".gz", ".rar", ".7z", ".iso", ".img"],
  Code: [".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".html", ".css", ".json", ".yml"],
  Design: [".psd", ".ai", ".fig", ".sketch", ".xd"],
  Models: [".obj", ".fbx", ".stl", ".glb", ".gltf", ".blend"],
  Fonts: [".ttf", ".otf", ".woff", ".woff2"],
  Executables: [".exe", ".dmg", ".deb", ".appimage", ".msi"],
};

const PACKS = {
  downloads: `IMG_4032.jpg
Screenshot 2026-08-01.png
invoice-q3.pdf
track.mp3
notes.txt
archive.zip
main.py
holiday.mkv
resume.docx
random.dat
photo.heic
book.epub
model.stl
Untitled
setup.exe
deck.pptx`,
  camera: `DSC_1182.NEF
IMG_9001.HEIC
PXL_20260801_120000.jpg
MVIMG_20260801_120001.jpg
screenshot-lockscreen.png`,
  desk: `assignment-2.pdf
lecture-notes.md
lab.py
results.csv
figure1.svg
bibliography.bib
thesis.tex
model.blend`,
};

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function smart(name) {
  if (/screenshot|screen[ _-]?shot|img_\d|dsc_|pxl_|mvimg/i.test(name)) return "Images";
  if (/invoice|receipt|statement|resume|\bcv\b|assignment|thesis/i.test(name)) return "Documents";
  return "";
}

function category(name) {
  const ext = extOf(name);
  for (const [cat, list] of Object.entries(RULES)) {
    if (list.includes(ext)) return cat;
  }
  return smart(name) || "Other";
}

function plan() {
  const names = document
    .querySelector("#names")
    .value.split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const byDate = document.querySelector("#by-date").checked;
  const counts = {};
  const rows = names.map((name) => {
    const cat = category(name);
    counts[cat] = (counts[cat] || 0) + 1;
    const dest = byDate ? `${cat}/YYYY/MM/${name}` : `${cat}/${name}`;
    return `<tr><td>${name}</td><td>${cat}</td><td>${dest}</td></tr>`;
  });
  document.querySelector("#out").innerHTML = rows.join("") || `<tr><td colspan="3">Nothing to plan.</td></tr>`;
  const bits = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([cat, n]) => `${cat} ${n}`);
  document.querySelector("#stats").textContent = names.length
    ? `${names.length} files · ${bits.join(" · ")}`
    : "";
}

document.querySelector("#plan").addEventListener("click", plan);
document.querySelector("#by-date").addEventListener("change", plan);
document.querySelectorAll("[data-pack]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelector("#names").value = PACKS[btn.dataset.pack] || "";
    plan();
  });
});
document.querySelector("#names").value = PACKS.downloads;
plan();
