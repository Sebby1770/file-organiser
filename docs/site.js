const RELEASE_API = "https://api.github.com/repos/Sebby1770/file-organiser/releases/latest";
const REPO_RELEASES = "https://github.com/Sebby1770/file-organiser/releases";
const PRO_REQUEST = "https://github.com/Sebby1770/file-organiser/issues/new?template=pro-early-access.yml";

function platform() {
  const value = `${navigator.userAgent || ""} ${navigator.platform || ""}`;
  if (/Mac|iPhone|iPad/i.test(value)) return { key: "macos", label: "macOS" };
  if (/Win/i.test(value)) return { key: "windows", label: "Windows" };
  if (/Linux/i.test(value)) return { key: "linux", label: "Linux" };
  return { key: "", label: "your OS" };
}

function isTrustedReleaseUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" && url.pathname.startsWith("/Sebby1770/file-organiser/releases/");
  } catch (_error) {
    return false;
  }
}

function assetForPlatform(assets, key) {
  const hints = {
    macos: ["macos", ".app", "darwin"],
    windows: ["windows", ".exe", "win64"],
    linux: ["linux", "x86_64", "amd64"],
  };
  const wanted = hints[key] || [];
  return (assets || []).find((asset) => {
    const name = String(asset.name || "").toLowerCase();
    return wanted.some((hint) => name.includes(hint));
  });
}

function setReleaseStatus(message) {
  document.querySelectorAll("[data-release-status]").forEach((node) => {
    node.textContent = message;
  });
}

async function resolveRelease() {
  const links = [...document.querySelectorAll(".release-aware")];
  if (!links.length && !document.querySelector("[data-release-status]")) return;
  const current = platform();
  try {
    const response = await fetch(RELEASE_API, { headers: { Accept: "application/vnd.github+json" } });
    if (response.status === 404) {
      setReleaseStatus("No packaged GitHub Release is published yet. Use the source preview; checkout is not live.");
      return;
    }
    if (!response.ok) throw new Error(`GitHub returned ${response.status}`);
    const release = await response.json();
    const asset = assetForPlatform(release.assets, current.key);
    if (!asset || !isTrustedReleaseUrl(asset.browser_download_url)) {
      setReleaseStatus(`${release.tag_name || "A release"} exists, but no verified ${current.label} asset was found. See install options.`);
      return;
    }
    const target = `./thanks.html?intent=download&name=${encodeURIComponent(asset.name)}&next=${encodeURIComponent(asset.browser_download_url)}`;
    links.forEach((link) => {
      link.href = target;
      link.textContent = `Download for ${current.label}`;
    });
    setReleaseStatus(`${release.tag_name || "Latest release"} has an unsigned ${current.label} test build. Platform warnings may appear.`);
  } catch (_error) {
    setReleaseStatus("Release availability could not be verified. Use the source instructions instead of assuming a download exists.");
    links.forEach((link) => {
      link.href = "./install.html";
      link.textContent = "View install options";
    });
  }
}

function configureProFlow() {
  document.querySelectorAll(".pro-interest").forEach((link) => {
    link.href = `./thanks.html?intent=pro&next=${encodeURIComponent(PRO_REQUEST)}`;
  });
}

function configureThanksPage() {
  const panel = document.querySelector("[data-thanks-panel]");
  if (!panel) return;
  const query = new URLSearchParams(location.search);
  const intent = query.get("intent");
  const next = query.get("next") || "";
  const title = panel.querySelector("[data-thanks-title]");
  const copy = panel.querySelector("[data-thanks-copy]");
  const action = panel.querySelector("[data-thanks-action]");
  if (intent === "download" && isTrustedReleaseUrl(next)) {
    title.textContent = "Thanks for trying the preview.";
    copy.textContent = "Your test build is unsigned. Keep a backup, expect an operating-system warning, and begin with a folder you understand.";
    action.textContent = `Download ${query.get("name") || "the release asset"}`;
    action.href = next;
    action.rel = "nofollow";
    return;
  }
  if (intent === "pro" && next === PRO_REQUEST) {
    title.textContent = "Thanks for helping shape Pro.";
    copy.textContent = "Nothing has been submitted yet. Continue to GitHub to send the early-access request; do not include private filenames or payment details.";
    action.textContent = "Continue to the GitHub request";
    action.href = next;
    return;
  }
  title.textContent = "Thanks for your interest.";
  copy.textContent = "Choose a safe next step below. No form was submitted and no personal information was collected on this page.";
  action.textContent = "View install options";
  action.href = "./install.html";
}

function validMeasurementId(value) {
  return /^G-[A-Z0-9]+$/.test(String(value || ""));
}

function loadAnalytics(config) {
  const measurementId = config.measurementId;
  window.dataLayer = window.dataLayer || [];
  window.gtag = function gtag() {
    window.dataLayer.push(arguments);
  };
  window.gtag("js", new Date());
  window.gtag("config", measurementId, { anonymize_ip: true });
  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
  document.head.append(script);
}

function showAnalyticsChoice(config) {
  document.querySelector(".consent-banner")?.remove();
  if (!validMeasurementId(config.measurementId)) {
    const notice = document.createElement("aside");
    notice.className = "consent-banner";
    const text = document.createElement("p");
    text.textContent = "Google Analytics 4 is not configured, so this site is not loading it or sending analytics events.";
    const actions = document.createElement("div");
    actions.className = "consent-actions";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => notice.remove());
    actions.append(close);
    notice.append(text, actions);
    document.body.append(notice);
    return;
  }
  const banner = document.createElement("aside");
  banner.className = "consent-banner";
  banner.setAttribute("aria-label", "Optional Google Analytics choice");
  const copy = document.createElement("p");
  copy.textContent = "Allow optional Google Analytics 4 for public website pages? The site works without it. Desktop scans, filenames, paths, and cleanup actions are excluded.";
  const actions = document.createElement("div");
  actions.className = "consent-actions";
  const decline = document.createElement("button");
  decline.type = "button";
  decline.textContent = "No thanks";
  const accept = document.createElement("button");
  accept.type = "button";
  accept.className = "accept";
  accept.textContent = "Allow analytics";
  actions.append(decline, accept);
  banner.append(copy, actions);
  document.body.append(banner);
  decline.addEventListener("click", () => {
    localStorage.setItem("fo-analytics-consent", "no");
    banner.remove();
  });
  accept.addEventListener("click", () => {
    localStorage.setItem("fo-analytics-consent", "yes");
    banner.remove();
    loadAnalytics(config);
  });
}

function configureAnalytics() {
  const config = window.FILE_ORGANISER_ANALYTICS || {};
  const settings = document.querySelectorAll("[data-analytics-settings]");
  settings.forEach((button) => button.addEventListener("click", () => showAnalyticsChoice(config)));
  const footerBase = document.querySelector(".footer-base");
  if (footerBase && !settings.length) {
    const separator = document.createTextNode(" · ");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "analytics-settings";
    button.textContent = "Analytics choices";
    button.addEventListener("click", () => showAnalyticsChoice(config));
    footerBase.append(separator, button);
  }
  if (!validMeasurementId(config.measurementId)) return;
  const choice = localStorage.getItem("fo-analytics-consent");
  if (choice === "yes") {
    loadAnalytics(config);
    return;
  }
  if (choice === "no") return;
  showAnalyticsChoice(config);
}

document.querySelectorAll("[data-year]").forEach((node) => {
  node.textContent = String(new Date().getFullYear());
});
configureProFlow();
configureThanksPage();
configureAnalytics();
resolveRelease();
