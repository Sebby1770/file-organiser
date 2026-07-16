"""Optional folder watching using watchdog (extra dependency)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rich.console import Console

from .organizer import organize
from .rules import load_rules


def _watchdog_available() -> bool:
    try:
        import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


def watch_folder(
    folder: Path,
    console: Console,
    *,
    rules: Optional[Dict[str, List[str]]] = None,
    config: Optional[Path] = None,
    recursive: bool = False,
    copy: bool = False,
    by_date: bool = False,
    min_size: int = 0,
    exclude: Sequence[str] | None = None,
    on_conflict: str = "rename",
    quiet: bool = False,
    debounce: float = 1.0,
) -> int:
    """Watch *folder* and auto-organize newly created/moved-in files.

    Requires the optional ``watchdog`` dependency::

        pip install file-organiser[watch]

    Returns process exit code (0 = normal, 1 = error, 130 = interrupt).
    """
    if not _watchdog_available():
        console.print(
            "[red]watchdog is not installed.[/red]\n"
            "Install the optional extra:\n"
            "  [bold]pip install file-organiser[watch][/bold]"
        )
        return 1

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 1

    if rules is None:
        rules = load_rules(config)

    # Capture for nested class
    opts = {
        "recursive": recursive,
        "copy": copy,
        "by_date": by_date,
        "min_size": min_size,
        "exclude": list(exclude or []),
        "on_conflict": on_conflict,
        "quiet": quiet,
    }

    class OrganizeHandler(FileSystemEventHandler):
        def __init__(self) -> None:
            super().__init__()
            self._last_run = 0.0

        def on_created(self, event):  # type: ignore[no-untyped-def]
            if event.is_directory:
                return
            self._maybe_organize()

        def on_moved(self, event):  # type: ignore[no-untyped-def]
            if event.is_directory:
                return
            self._maybe_organize()

        def _maybe_organize(self) -> None:
            now = time.monotonic()
            if now - self._last_run < debounce:
                return
            self._last_run = now
            # Small delay so the file is fully written
            time.sleep(0.3)
            try:
                if not quiet:
                    console.print("[dim]Change detected — organizing...[/dim]")
                organize(
                    folder,
                    rules,  # type: ignore[arg-type]
                    console,
                    dry_run=False,
                    recursive=opts["recursive"],
                    copy=opts["copy"],
                    by_date=opts["by_date"],
                    min_size=opts["min_size"],
                    exclude=opts["exclude"],
                    on_conflict=opts["on_conflict"],  # type: ignore[arg-type]
                    quiet=quiet,
                )
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Watch organize error:[/red] {e}")

    handler = OrganizeHandler()
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=recursive)
    observer.start()

    if not quiet:
        console.print(
            f"[green]Watching[/green] [cyan]{folder}[/cyan] "
            f"(recursive={recursive}). Press Ctrl+C to stop."
        )

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watch...[/yellow]")
        observer.stop()
        observer.join(timeout=5)
        return 130
    finally:
        if observer.is_alive():
            observer.stop()
            observer.join(timeout=5)

    return 0
