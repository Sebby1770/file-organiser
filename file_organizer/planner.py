"""Pure planning logic: scan, classify, deduplicate, and reserve destinations."""

from __future__ import annotations

import os
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .errors import PlanningError
from .models import (
    DuplicateGroup,
    OrganizationPlan,
    PlannedMove,
    SkippedItem,
)
from .rules import RuleSet, default_rules
from .scanner import scan_files
from .utils import ensure_no_symlink_components, fingerprint_file, resolve_root

COLLISION_STRATEGIES = ("rename", "skip", "error")
DUPLICATE_STRATEGIES = ("keep", "skip", "error")


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _renamed_candidate(path: Path, counter: int) -> Path:
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    return path.with_name(f"{stem} ({counter}){suffix}")


def _reservation_key(path: Path) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _reserve_destination(
    preferred: Path,
    reserved: set[str],
    collision_strategy: str,
) -> Path | None:
    preferred_key = _reservation_key(preferred)
    if not _exists(preferred) and preferred_key not in reserved:
        reserved.add(preferred_key)
        return preferred
    if collision_strategy == "skip":
        return None
    if collision_strategy == "error":
        raise PlanningError(f"Destination collision: {preferred}")
    counter = 1
    while True:
        candidate = _renamed_candidate(preferred, counter)
        candidate_key = _reservation_key(candidate)
        if not _exists(candidate) and candidate_key not in reserved:
            reserved.add(candidate_key)
            return candidate
        counter += 1


def create_plan(
    root: str | os.PathLike[str],
    rules: RuleSet | None = None,
    *,
    recursive: bool = False,
    collision_strategy: str = "rename",
    duplicate_strategy: str = "keep",
    ignore_patterns: Iterable[str] = (),
    include_hidden: bool | None = None,
    protected_paths: Iterable[Path] = (),
) -> OrganizationPlan:
    """Create a deterministic, immutable plan without writing to disk."""

    if collision_strategy not in COLLISION_STRATEGIES:
        raise PlanningError(
            f"Unknown collision strategy {collision_strategy!r}; choose from "
            f"{', '.join(COLLISION_STRATEGIES)}"
        )
    if duplicate_strategy not in DUPLICATE_STRATEGIES:
        raise PlanningError(
            f"Unknown duplicate strategy {duplicate_strategy!r}; choose from "
            f"{', '.join(DUPLICATE_STRATEGIES)}"
        )

    target = resolve_root(root)
    active_rules = rules or default_rules()
    patterns = tuple(
        dict.fromkeys((*active_rules.ignore_patterns, *tuple(ignore_patterns)))
    )
    show_hidden = (
        active_rules.include_hidden if include_hidden is None else include_hidden
    )
    safe_protected: list[Path] = []
    for path in protected_paths:
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except FileNotFoundError:
            continue
        try:
            resolved.relative_to(target)
        except ValueError:
            continue
        safe_protected.append(resolved)

    sources, scan_skips = scan_files(
        target,
        recursive=recursive,
        ignore_patterns=patterns,
        include_hidden=show_hidden,
        managed_directories=active_rules.managed_categories,
        protected_paths=safe_protected,
    )

    fingerprints = {source: fingerprint_file(source, target) for source in sources}
    content_groups: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for source in sources:
        fingerprint = fingerprints[source]
        content_groups[(fingerprint.size, fingerprint.sha256)].append(source)
    duplicates = tuple(
        DuplicateGroup(size=size, sha256=sha256, files=tuple(paths))
        for (size, sha256), paths in sorted(
            content_groups.items(),
            key=lambda item: (
                item[0][0],
                item[0][1],
                tuple(path.relative_to(target).as_posix() for path in item[1]),
            ),
        )
        if len(paths) > 1
    )
    duplicate_canonical: dict[Path, Path] = {}
    for group in duplicates:
        canonical = group.files[0]
        for duplicate in group.files[1:]:
            duplicate_canonical[duplicate] = canonical
    if duplicate_strategy == "error" and duplicates:
        first = duplicates[0]
        names = ", ".join(path.relative_to(target).as_posix() for path in first.files)
        raise PlanningError(f"Duplicate content found: {names}")

    skipped = list(scan_skips)
    moves: list[PlannedMove] = []
    reserved: set[str] = set()
    for source in sources:
        duplicate_of = duplicate_canonical.get(source)
        if duplicate_of is not None and duplicate_strategy == "skip":
            skipped.append(
                SkippedItem(
                    source,
                    "duplicate",
                    f"same content as {duplicate_of.relative_to(target).as_posix()}",
                )
            )
            continue

        relative = source.relative_to(target)
        category = active_rules.category_for(source.name)
        destination = target / category / (relative if recursive else source.name)
        ensure_no_symlink_components(destination, target, leaf=False)
        final_destination = _reserve_destination(
            destination, reserved, collision_strategy
        )
        if final_destination is None:
            skipped.append(
                SkippedItem(
                    source,
                    "collision",
                    f"destination exists: {destination.relative_to(target).as_posix()}",
                )
            )
            continue
        moves.append(
            PlannedMove(
                source=source,
                destination=final_destination,
                category=category,
                fingerprint=fingerprints[source],
                duplicate_of=duplicate_of,
            )
        )

    return OrganizationPlan(
        root=target,
        recursive=recursive,
        collision_strategy=collision_strategy,
        duplicate_strategy=duplicate_strategy,
        include_hidden=show_hidden,
        ignore_patterns=patterns,
        rules_source=active_rules.source,
        scanned_files=len(sources),
        moves=tuple(moves),
        skipped=tuple(skipped),
        duplicates=duplicates,
    )
