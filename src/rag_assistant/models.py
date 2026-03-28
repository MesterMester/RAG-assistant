from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class DocumentChunk:
    chunk_id: str
    source_path: str
    title: str
    text: str
    tokens_estimate: int
    source_type: str = "document"
    record_id: str | None = None
    entity_type: str | None = None
    organization: str | None = None
    team: str | None = None
    project: str | None = None
    case_name: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "DocumentChunk":
        payload = dict(payload)
        payload.setdefault("source_type", "document")
        payload.setdefault("record_id", None)
        payload.setdefault("entity_type", None)
        payload.setdefault("organization", None)
        payload.setdefault("team", None)
        payload.setdefault("project", None)
        payload.setdefault("case_name", None)
        payload.setdefault("tags", [])
        return cls(**payload)


@dataclass(slots=True)
class KnowledgeRecord:
    record_id: str
    title: str
    summary: str
    content: str
    source_type: str
    entity_type: str
    status: str
    organization: str = ""
    team: str = ""
    project: str = ""
    case_name: str = ""
    parent_id: str = ""
    related_people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    decision_needed: bool = False
    decision_context: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    start_at: str | None = None
    due_at: str | None = None
    deadline: str | None = None
    event_at: str | None = None
    planning_bucket: str = ""
    planning_order: int | None = None
    focus_rank: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "KnowledgeRecord":
        payload = dict(payload)
        payload.setdefault("tags", [])
        payload.setdefault("relations", [])
        payload.setdefault("related_people", [])
        payload.setdefault("summary", "")
        payload.setdefault("content", "")
        payload.setdefault("organization", "")
        payload.setdefault("team", "")
        payload.setdefault("project", "")
        payload.setdefault("case_name", payload.pop("case", ""))
        payload.setdefault("parent_id", "")
        payload.setdefault("status", "inbox")
        payload.setdefault("entity_type", payload.pop("kind", "note"))
        payload.setdefault("source_type", "manual")
        payload.setdefault("decision_needed", False)
        payload.setdefault("decision_context", "")
        payload.setdefault("created_at", utc_now_iso())
        payload.setdefault("updated_at", payload["created_at"])
        payload.setdefault("start_at", None)
        payload.setdefault("due_at", None)
        payload.setdefault("deadline", None)
        payload.setdefault("event_at", None)
        payload.setdefault("planning_bucket", "")
        payload.setdefault("planning_order", None)
        payload.setdefault("focus_rank", None)
        return cls(**payload)

    def to_search_text(self) -> str:
        parts = [
            self.entity_type,
            self.title,
            self.organization,
            self.team,
            self.project,
            self.case_name,
            self.summary,
            self.content,
            " ".join(self.related_people),
            " ".join(self.tags),
            " ".join(self.relations),
            self.decision_context,
        ]
        return "\n".join(part for part in parts if part).strip()

    def to_table_row(self) -> dict:
        return {
            "record_id": self.record_id,
            "entity_type": self.entity_type,
            "title": self.title,
            "summary": self.summary,
            "organization": self.organization,
            "team": self.team,
            "project": self.project,
            "case": self.case_name,
            "status": self.status,
            "parent_id": self.parent_id,
            "people": ", ".join(self.related_people),
            "tags": ", ".join(self.tags),
            "decision_needed": self.decision_needed,
            "start_at": self.start_at or "",
            "due_at": self.due_at or "",
            "deadline": self.deadline or "",
            "event_at": self.event_at or "",
            "planning_bucket": self.planning_bucket,
            "focus_rank": self.focus_rank if self.focus_rank is not None else "",
            "updated_at": self.updated_at,
        }
