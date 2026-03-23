from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from rag_assistant.models import KnowledgeRecord, utc_now_iso


SLUG_RE = re.compile(r"[^a-z0-9]+")


def build_record_id(title: str) -> str:
    slug = SLUG_RE.sub("-", title.strip().lower()).strip("-")
    if not slug:
        slug = "record"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_records(path: Path) -> list[KnowledgeRecord]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [KnowledgeRecord.from_dict(item) for item in payload.get("records", [])]


def save_records(path: Path, records: list[KnowledgeRecord]) -> None:
    _ensure_parent(path)
    payload = {
        "version": 1,
        "records": [record.to_dict() for record in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_record(path: Path, record: KnowledgeRecord) -> KnowledgeRecord:
    records = load_records(path)
    now = utc_now_iso()
    updated = None

    for index, existing in enumerate(records):
        if existing.record_id == record.record_id:
            record.created_at = existing.created_at
            record.updated_at = now
            records[index] = record
            updated = record
            break

    if updated is None:
        record.created_at = record.created_at or now
        record.updated_at = now
        records.append(record)
        updated = record

    records.sort(key=lambda item: item.updated_at, reverse=True)
    save_records(path, records)
    return updated
