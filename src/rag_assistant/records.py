from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from rag_assistant.history import append_event, build_event
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
        normalized_edges: list[dict] = []
        for item in record.graph_edges or []:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("target_id", "")).strip()
            relation_type = str(item.get("relation_type", "")).strip() or "related_to"
            label = str(item.get("label", "")).strip()
            if not target_id or target_id == record.record_id:
                continue
            normalized_edges.append(
                {
                    "target_id": target_id,
                    "relation_type": relation_type,
                    "label": label,
                }
            )
        if not normalized_edges and record.relations:
            normalized_edges = [
                {"target_id": relation_id.strip(), "relation_type": "related_to", "label": ""}
                for relation_id in record.relations
                if relation_id.strip() and relation_id.strip() != record.record_id
            ]
        deduped_edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for item in normalized_edges:
            edge_key = (item["target_id"], item["relation_type"], item["label"])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            deduped_edges.append(item)
        record.graph_edges = deduped_edges
        seen_relations: set[str] = set()
        record.relations = []
        for item in record.graph_edges:
            target_id = item.get("target_id", "").strip()
            if target_id and target_id not in seen_relations:
                seen_relations.add(target_id)
                record.relations.append(target_id)

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


def replace_records(path: Path, records: list[KnowledgeRecord], history_path: Path | None = None, source: str = "ui") -> list[KnowledgeRecord]:
    existing_records = load_records(path)
    existing_lookup = {record.record_id: record for record in existing_records}
    now = utc_now_iso()
    final_records: list[KnowledgeRecord] = []
    seen_ids: set[str] = set()

    for record in records:
        existing = existing_lookup.get(record.record_id)
        if existing:
            before = existing.to_dict()
            record.created_at = existing.created_at
            if before != record.to_dict():
                record.updated_at = now
                if history_path is not None:
                    append_event(history_path, build_event(record.record_id, before, record.to_dict(), source))
            else:
                record.updated_at = existing.updated_at
        else:
            record.created_at = record.created_at or now
            record.updated_at = now
            if history_path is not None:
                append_event(history_path, build_event(record.record_id, None, record.to_dict(), source))
        final_records.append(record)
        seen_ids.add(record.record_id)

    for existing in existing_records:
        if existing.record_id in seen_ids:
            continue
        if history_path is not None:
            append_event(history_path, build_event(existing.record_id, existing.to_dict(), None, source))

    final_records.sort(key=lambda item: item.updated_at, reverse=True)
    save_records(path, final_records)
    return final_records


def upsert_record(path: Path, record: KnowledgeRecord, history_path: Path | None = None, source: str = "ui") -> KnowledgeRecord:
    existing_records = load_records(path)
    lookup = {item.record_id: item for item in existing_records}
    lookup[record.record_id] = record
    final_records = [lookup[item.record_id] for item in existing_records if item.record_id in lookup and not lookup.pop(item.record_id, None)]
    # rebuild in a stable way
    merged: list[KnowledgeRecord] = []
    seen: set[str] = set()
    for item in existing_records + [record]:
        candidate = record if item.record_id == record.record_id else item
        if candidate.record_id in seen:
            continue
        seen.add(candidate.record_id)
        merged.append(candidate)
    replace_records(path, merged, history_path=history_path, source=source)
    refreshed = load_records(path)
    return next(item for item in refreshed if item.record_id == record.record_id)


def delete_record(path: Path, record_id: str, history_path: Path | None = None, source: str = "ui") -> bool:
    records = load_records(path)
    filtered = [record for record in records if record.record_id != record_id]
    if len(filtered) == len(records):
        return False
    replace_records(path, filtered, history_path=history_path, source=source)
    return True
