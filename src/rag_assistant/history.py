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
