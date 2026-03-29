from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(slots=True)
class BackupResult:
    archive_path: Path
    manifest_path: Path
    source_dir: Path
    backup_dir: Path
    created_at: str
    size_bytes: int
    file_count: int


def _should_skip(path: Path, backup_dir: Path) -> bool:
    try:
        path.relative_to(backup_dir)
        return True
    except ValueError:
        return False


def _iter_files(root: Path, backup_dir: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not _should_skip(path, backup_dir)
    ]


def create_backup(source_dir: Path, backup_dir: Path) -> BackupResult:
    source_dir = source_dir.resolve()
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = utc_stamp()
    archive_file = backup_dir / f"ragdb-backup-{stamp}.zip"
    manifest_path = backup_dir / f"ragdb-backup-{stamp}.json"
    files = _iter_files(source_dir, backup_dir)

    with ZipFile(archive_file, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.relative_to(source_dir))

    manifest = {
        "created_at": stamp,
        "source_dir": str(source_dir),
        "archive_path": str(archive_file),
        "file_count": len(files),
        "files": [str(path.relative_to(source_dir)) for path in files],
        "size_bytes": archive_file.stat().st_size,
        "excluded_prefixes": [str(backup_dir.relative_to(source_dir))],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return BackupResult(
        archive_path=archive_file,
        manifest_path=manifest_path,
        source_dir=source_dir,
        backup_dir=backup_dir,
        created_at=stamp,
        size_bytes=archive_file.stat().st_size,
        file_count=len(files),
    )
