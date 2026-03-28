from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rag_assistant.config import AppConfig
from rag_assistant.models import KnowledgeRecord
from rag_assistant.proposals import ProposedChange, ProposedChangeBatch, SourceEvidence
from rag_assistant.records import load_records


@dataclass(slots=True)
class DryRunDecision:
    action: str
    matched_record_id: str | None
    matched_title: str | None
    reason: str
    sources_to_attach: list[SourceEvidence]


def dry_run_upsert_batch(
    batch: ProposedChangeBatch,
    source_dir: Path,
    config: AppConfig,
) -> list[tuple[ProposedChange, DryRunDecision]]:
    records = load_records(config.manual_records_path_for(source_dir))
    return [(change, decide_change_action(change, records)) for change in batch.changes]


def decide_change_action(change: ProposedChange, records: list[KnowledgeRecord]) -> DryRunDecision:
    record = change.record
    if record is None:
        return DryRunDecision(
            action="ignore",
            matched_record_id=None,
            matched_title=None,
            reason="No normalized record payload was provided.",
            sources_to_attach=[],
        )

    match = find_matching_record(change, records)
    if match is None:
        return DryRunDecision(
            action="create",
            matched_record_id=None,
            matched_title=None,
            reason="No matching record found in the current RAG store.",
            sources_to_attach=list(change.sources),
        )

    updates: list[str] = []
    if normalized(record.summary) and normalized(record.summary) != normalized(match.summary):
        updates.append("summary")
    if normalized(record.content) and normalized(record.content) != normalized(match.content):
        updates.append("content")
    if record.entity_type and record.entity_type != match.entity_type:
        updates.append("entity_type")
    if merge_candidates(record.tags, match.tags):
        updates.append("tags")
    if merge_candidates(record.relations, match.relations):
        updates.append("relations")
    if record.status and record.status != match.status:
        updates.append("status")
    if record.project and record.project != (match.project or ""):
        updates.append("project")
    if record.case_name and record.case_name != (match.case_name or ""):
        updates.append("case_name")

    if updates:
        return DryRunDecision(
            action="update",
            matched_record_id=match.record_id,
            matched_title=match.title,
            reason=f"Matching record found; changed fields: {', '.join(updates)}.",
            sources_to_attach=list(change.sources),
        )

    return DryRunDecision(
        action="attach_source",
        matched_record_id=match.record_id,
        matched_title=match.title,
        reason="Matching record found and normalized content looks equivalent.",
        sources_to_attach=list(change.sources),
    )


def find_matching_record(change: ProposedChange, records: list[KnowledgeRecord]) -> KnowledgeRecord | None:
    record = change.record
    if record is None:
        return None

    target_hint = normalized(change.target.match_hint)
    title = normalized(record.title)

    for existing in records:
        if change.target.record_id and existing.record_id == change.target.record_id:
            return existing

    if title:
        title_matches = [existing for existing in records if normalized(existing.title) == title]
        if len(title_matches) == 1:
            return title_matches[0]

    if target_hint:
        hint_matches = [existing for existing in records if normalized(existing.title) == target_hint]
        if len(hint_matches) == 1:
            return hint_matches[0]

    return None


def normalized(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def merge_candidates(incoming: list[str], existing: list[str]) -> list[str]:
    existing_set = {item.strip().lower() for item in existing}
    return [item for item in incoming if item.strip().lower() not in existing_set]
