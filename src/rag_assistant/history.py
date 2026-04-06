from __future__ import annotations

import json
import uuid
from pathlib import Path

from rag_assistant.models import utc_now_iso


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def changed_fields(before: dict | None, after: dict | None) -> list[str]:
    keys = sorted(set((before or {}).keys()) | set((after or {}).keys()))
    return [key for key in keys if (before or {}).get(key) != (after or {}).get(key)]


def derive_action(before: dict | None, after: dict | None, fields: list[str]) -> str:
    if before is None and after is not None:
        return "create"
    if before is not None and after is None:
        return "delete"
    if "planning_bucket" in fields:
        return "move"
    return "update"


def build_event(record_id: str, before: dict | None, after: dict | None, source: str) -> dict:
    fields = changed_fields(before, after)
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": utc_now_iso(),
        "record_id": record_id,
        "action_type": derive_action(before, after, fields),
        "source": source,
        "changed_fields": fields,
        "before": before,
        "after": after,
    }
    if before is not None and after is not None and "planning_bucket" in fields:
        event["movement"] = {
            "from": (before or {}).get("planning_bucket", ""),
            "to": (after or {}).get("planning_bucket", ""),
        }
    return event


def append_event(path: Path, event: dict) -> None:
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def summarize_event(event: dict) -> str:
    action = event.get("action_type", "-")
    changed_fields = event.get("changed_fields", [])
    before = event.get("before") or {}
    after = event.get("after") or {}
    if action == "move":
        movement = event.get("movement") or {}
        return f"Áthelyezés: {movement.get('from', '') or 'nincs'} -> {movement.get('to', '') or 'nincs'}"
    if action == "create":
        return f"Létrehozás: {after.get('title', event.get('record_id', '-'))}"
    if action == "delete":
        return f"Törlés: {before.get('title', event.get('record_id', '-'))}"
    if action == "export":
        return f"Export: {(after or {}).get('path', '')}"
    details: list[str] = []
    for field in changed_fields[:5]:
        details.append(f"{field}: {before.get(field, '') or '-'} -> {after.get(field, '') or '-'}")
    return " | ".join(details) if details else "Módosítás"
