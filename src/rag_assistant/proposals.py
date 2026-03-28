from __future__ import annotations

from dataclasses import asdict, dataclass, field

from rag_assistant.models import utc_now_iso


@dataclass(slots=True)
class SourceEvidence:
    source_type: str
    source_item_id: str
    source_label: str
    locator: str | None = None
    observed_at: str | None = None
    snippet: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "SourceEvidence":
        payload = dict(payload)
        payload.setdefault("locator", None)
        payload.setdefault("observed_at", None)
        payload.setdefault("snippet", None)
        payload.setdefault("confidence", None)
        return cls(**payload)


@dataclass(slots=True)
class ProposedRelation:
    relation_type: str
    from_ref: str
    to_ref: str
    label: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "ProposedRelation":
        payload = dict(payload)
        payload.setdefault("label", None)
        return cls(**payload)


@dataclass(slots=True)
class ProposedTarget:
    record_id: str | None = None
    external_key: str | None = None
    match_hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ProposedTarget":
        payload = dict(payload or {})
        payload.setdefault("record_id", None)
        payload.setdefault("external_key", None)
        payload.setdefault("match_hint", None)
        return cls(**payload)


@dataclass(slots=True)
class ProposedRecord:
    title: str
    summary: str = ""
    content: str = ""
    entity_type: str = "note"
    status: str | None = None
    organization: str | None = None
    team: str | None = None
    project: str | None = None
    case_name: str | None = None
    parent_id: str | None = None
    related_people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    decision_needed: bool | None = None
    decision_context: str | None = None
    start_at: str | None = None
    due_at: str | None = None
    deadline: str | None = None
    event_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ProposedRecord | None":
        if payload is None:
            return None
        payload = dict(payload)
        payload.setdefault("summary", "")
        payload.setdefault("content", "")
        payload.setdefault("entity_type", "note")
        payload.setdefault("status", None)
        payload.setdefault("organization", None)
        payload.setdefault("team", None)
        payload.setdefault("project", None)
        payload.setdefault("case_name", None)
        payload.setdefault("parent_id", None)
        payload.setdefault("related_people", [])
        payload.setdefault("tags", [])
        payload.setdefault("relations", [])
        payload.setdefault("decision_needed", None)
        payload.setdefault("decision_context", None)
        payload.setdefault("start_at", None)
        payload.setdefault("due_at", None)
        payload.setdefault("deadline", None)
        payload.setdefault("event_at", None)
        return cls(**payload)


@dataclass(slots=True)
class ProposedChange:
    change_id: str
    operation: str
    target: ProposedTarget = field(default_factory=ProposedTarget)
    record: ProposedRecord | None = None
    relations: list[ProposedRelation] = field(default_factory=list)
    sources: list[SourceEvidence] = field(default_factory=list)
    confidence: float | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "operation": self.operation,
            "target": self.target.to_dict(),
            "record": self.record.to_dict() if self.record else None,
            "relations": [relation.to_dict() for relation in self.relations],
            "sources": [source.to_dict() for source in self.sources],
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ProposedChange":
        payload = dict(payload)
        payload.setdefault("target", {})
        payload.setdefault("record", None)
        payload.setdefault("relations", [])
        payload.setdefault("sources", [])
        payload.setdefault("confidence", None)
        payload.setdefault("reason", "")
        return cls(
            change_id=payload["change_id"],
            operation=payload["operation"],
            target=ProposedTarget.from_dict(payload["target"]),
            record=ProposedRecord.from_dict(payload["record"]),
            relations=[ProposedRelation.from_dict(item) for item in payload["relations"]],
            sources=[SourceEvidence.from_dict(item) for item in payload["sources"]],
            confidence=payload["confidence"],
            reason=payload["reason"],
        )


@dataclass(slots=True)
class ProposedChangeBatch:
    batch_id: str
    producer: str
    changes: list[ProposedChange] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "producer": self.producer,
            "created_at": self.created_at,
            "changes": [change.to_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ProposedChangeBatch":
        payload = dict(payload)
        payload.setdefault("created_at", utc_now_iso())
        payload.setdefault("changes", [])
        return cls(
            batch_id=payload["batch_id"],
            producer=payload["producer"],
            created_at=payload["created_at"],
            changes=[ProposedChange.from_dict(item) for item in payload["changes"]],
        )
