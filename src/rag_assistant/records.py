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


def delete_record(path: Path, record_id: str) -> bool:
    records = load_records(path)
    filtered = [record for record in records if record.record_id != record_id]
    if len(filtered) == len(records):
        return False
    save_records(path, filtered)
    return True


def normalize_records(records: list[KnowledgeRecord]) -> tuple[list[KnowledgeRecord], int]:
    lookup = {record.record_id: record for record in records}
    normalized: list[KnowledgeRecord] = []
    changed = 0

    def resolve_reference(value: str, expected_type: str) -> str:
        candidate = (value or "").strip()
        if candidate in lookup and lookup[candidate].entity_type == expected_type:
            return lookup[candidate].title.strip()
        return candidate

    for record in records:
        before = record.to_dict()
        record.title = record.title.strip()
        record.summary = record.summary.strip()
        record.organization = resolve_reference(record.organization, "organization")
        record.team = resolve_reference(record.team, "team")
        record.project = resolve_reference(record.project, "project")
        record.case_name = resolve_reference(record.case_name, "case")
        record.parent_id = (record.parent_id or "").strip()
        record.planning_bucket = (record.planning_bucket or "").strip()

        if record.entity_type == "organization" and not record.organization:
            record.organization = record.title
        if record.entity_type == "team" and not record.team:
            record.team = record.title
        if record.entity_type == "project" and not record.project:
            record.project = record.title
        if record.entity_type == "case" and not record.case_name:
            record.case_name = record.title

        if before != record.to_dict():
            changed += 1
        normalized.append(record)

    return normalized, changed


def normalize_record_store(path: Path) -> int:
    records = load_records(path)
    normalized, changed = normalize_records(records)
    if changed:
        save_records(path, normalized)
    return changed


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
