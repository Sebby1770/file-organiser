const RULES = {
  Images: [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".ico", ".bmp", ".tiff", ".raw"],
  Documents: [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".tex", ".pages"],
  Ebooks: [".epub", ".mobi", ".azw", ".azw3"],
  Spreadsheets: [".xls", ".xlsx", ".csv", ".ods", ".tsv"],
  Presentations: [".ppt", ".pptx", ".odp", ".key"],
  Videos: [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"],
  Audio: [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
  Archives: [".zip", ".tar", ".gz", ".rar", ".7z", ".iso"],
  Code: [".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".html", ".css", ".json"],
  Design: [".psd", ".ai", ".fig", ".sketch"],
  Models: [".obj", ".fbx", ".stl", ".glb", ".gltf", ".blend"],
  Fonts: [".ttf", ".otf", ".woff", ".woff2"],
  Executables: [".exe", ".dmg", ".deb", ".appimage"],
};

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function smart(name) {
  if (/screenshot|screen[ _-]?shot|img_\d|dsc_/i.test(name)) return "Images";
  if (/invoice|receipt|statement|resume|\bcv\b/i.test(name)) return "Documents";
  return "";
}

function category(name) {
  const ext = extOf(name);
  for (const [cat, list] of Object.entries(RULES)) {
    if (list.includes(ext)) return cat;
  }
  return smart(name) || "Other";
}

document.querySelector("#plan").addEventListener("click", () => {
  const names = document
    .querySelector("#names")
    .value.split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const rows = names.map((name) => {
    const cat = category(name);
    return `<tr><td>${name}</td><td>${cat}</td><td>${cat}/${name}</td></tr>`;
  });
  document.querySelector("#out").innerHTML = rows.join("") || `<tr><td colspan="3">Nothing to plan.</td></tr>`;
});
