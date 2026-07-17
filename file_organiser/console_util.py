"""Console helpers including NO_COLOR support."""
from __future__ import annotations

import os
from typing import Optional

from rich.console import Console


def env_no_color() -> bool:
    """Return True when colour output should be disabled.

    Honours the NO_COLOR convention (https://no-color.org/): any non-empty
    value of the ``NO_COLOR`` environment variable disables colour.
    """
    return bool(os.environ.get("NO_COLOR", "").strip())


def make_console(
    *,
    quiet: bool = False,
    force_terminal: Optional[bool] = None,
) -> Console:
    """Create a Rich Console that respects ``NO_COLOR``.

    When NO_COLOR is set, forces ``no_color=True`` so markup is stripped.
    """
    no_color = env_no_color()
    kwargs: dict = {"quiet": quiet}
    if no_color:
        kwargs["no_color"] = True
        kwargs["force_terminal"] = False if force_terminal is None else force_terminal
    elif force_terminal is not None:
        kwargs["force_terminal"] = force_terminal
    return Console(**kwargs)
