from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst"}


@dataclass(slots=True)
class SourceSummary:
    root: Path
    total_files: int
    total_bytes: int
    by_extension: dict[str, int]


def iter_source_files(source_dir: Path):
    for path in sorted(source_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def summarize_source_dir(source_dir: Path) -> SourceSummary:
    counts: Counter[str] = Counter()
    total_files = 0
    total_bytes = 0

    for path in iter_source_files(source_dir):
        total_files += 1
        total_bytes += path.stat().st_size
        counts[path.suffix.lower() or "<none>"] += 1

    return SourceSummary(
        root=source_dir,
        total_files=total_files,
        total_bytes=total_bytes,
        by_extension=dict(sorted(counts.items())),
    )
