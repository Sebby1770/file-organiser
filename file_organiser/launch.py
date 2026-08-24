"""Open File Organiser as a desktop app — no terminal required."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _pick_port(host: str, start: int = 8765, span: int = 24) -> int:
    for port in range(start, start + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return start


def _open_app_window(url: str) -> None:
    """Prefer a chromeless app window so this feels like software, not a website."""
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Vivaldi"):
            try:
                subprocess.Popen(
                    ["open", "-n", "-a", app, "--args", f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except OSError:
                continue
    elif sys.platform.startswith("win"):
        for exe in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ):
            if Path(exe).is_file():
                subprocess.Popen([exe, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    else:
        for bin_name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            try:
                subprocess.Popen(
                    [bin_name, f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except FileNotFoundError:
                continue
    webbrowser.open(url)


def _control_window(url: str) -> bool:
    """Tiny host window so a packaged binary has a Quit button and a dock presence."""
    try:
        import tkinter as tk
    except Exception:
        return False

    try:
        root = tk.Tk()
    except Exception:
        return False

    root.title("File Organiser")
    root.geometry("420x220")
    root.minsize(360, 180)
    bg, ink, gold = "#0c0e12", "#e8edf4", "#d4a054"
    root.configure(bg=bg)
    pad = {"bg": bg, "fg": ink, "font": ("IBM Plex Sans", 13)}
    tk.Label(root, text="FILE ORGANISER", fg=gold, bg=bg, font=("IBM Plex Sans", 11)).pack(pady=(22, 4))
    tk.Label(root, text="The app is open on this computer.\nNothing is uploaded.", **pad).pack()
    tk.Label(root, text=url, fg="#8b95a8", bg=bg, font=("Menlo", 11)).pack(pady=8)

    def reopen() -> None:
        _open_app_window(url)

    def quit_app() -> None:
        root.destroy()
        raise SystemExit(0)

    btn = {"bg": gold, "fg": bg, "relief": "flat", "font": ("IBM Plex Sans", 12), "padx": 14, "pady": 6}
    row = tk.Frame(root, bg=bg)
    row.pack(pady=12)
    tk.Button(row, text="Open app", command=reopen, **btn).pack(side="left", padx=6)
    tk.Button(row, text="Quit", command=quit_app, bg="#1c2230", fg=ink, relief="flat", padx=14, pady=6).pack(
        side="left", padx=6
    )
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()
    return True


def run_desktop(
    host: str = "127.0.0.1",
    port: int = 0,
    folder: Path | None = None,
    open_browser: bool = True,
    control_window: bool = True,
) -> int:
    """Start the local app and keep the process alive until the person quits."""
    from .app_server import serve_app

    if port <= 0:
        port = _pick_port(host)
    url = f"http://{host}:{port}"

    def server() -> None:
        serve_app(host, port, folder, open_browser=False)

    threading.Thread(target=server, daemon=True).start()
    time.sleep(0.35)
    if open_browser:
        _open_app_window(url)
        print(f"File Organiser  {url}")
    if control_window:
        if _control_window(url):
            return 0
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
