from __future__ import annotations

import re
import uuid
from pathlib import Path

from rag_assistant.proposals import (
    ProposedChange,
    ProposedChangeBatch,
    ProposedRecord,
    ProposedTarget,
    SourceEvidence,
)


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
HEADING_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_\-/]+)")


def import_obsidian_markdown(note_path: Path, vault_root: Path | None = None) -> ProposedChangeBatch:
    note_path = note_path.expanduser().resolve()
    vault_root = vault_root.expanduser().resolve() if vault_root else note_path.parent

    text = note_path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)

    relative_locator = build_locator(note_path, vault_root)
    source_item_id = str(frontmatter.get("id") or relative_locator)
    title = derive_title(note_path, frontmatter, body)
    tags = normalize_tags(frontmatter.get("tags"), body)
    relations = extract_wikilinks(body)

    record = ProposedRecord(
        title=title,
        summary=derive_summary(body),
        content=body.strip(),
        entity_type=str(frontmatter.get("entity_type") or frontmatter.get("kind") or "note"),
        status=optional_str(frontmatter.get("status")),
        organization=optional_str(frontmatter.get("organization")),
        team=optional_str(frontmatter.get("team")),
        project=optional_str(frontmatter.get("project")),
        case_name=optional_str(frontmatter.get("case") or frontmatter.get("case_name")),
        related_people=normalize_list(frontmatter.get("related_people")),
        tags=tags,
        relations=relations,
        decision_needed=optional_bool(frontmatter.get("decision_needed")),
        decision_context=optional_str(frontmatter.get("decision_context")),
        start_at=optional_str(frontmatter.get("start_at") or frontmatter.get("start")),
        due_at=optional_str(frontmatter.get("due_at") or frontmatter.get("due")),
        deadline=optional_str(frontmatter.get("deadline")),
        event_at=optional_str(frontmatter.get("event_at")),
    )
    evidence = SourceEvidence(
        source_type="obsidian",
        source_item_id=source_item_id,
        source_label=title,
        locator=relative_locator,
        observed_at=None,
        snippet=record.summary or None,
        confidence=1.0,
    )
    change = ProposedChange(
        change_id=f"obsidian-{uuid.uuid4().hex[:10]}",
        operation="create_record",
        target=ProposedTarget(
            record_id=None,
            external_key=source_item_id,
            match_hint=title,
        ),
        record=record,
        sources=[evidence],
        confidence=1.0,
        reason="Imported from Obsidian markdown note.",
    )
    return ProposedChangeBatch(
        batch_id=f"obsidian-batch-{uuid.uuid4().hex[:10]}",
        producer="obsidian_importer",
        changes=[change],
    )


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    frontmatter = parse_frontmatter_lines(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    if text.endswith("\n"):
        body += "\n"
    return frontmatter, body


def parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    data: dict[str, object] = {}
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()
        index += 1
        if not line or line.startswith("#") or ":" not in raw_line:
            continue

        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()

        if not value:
            items: list[str] = []
            while index < len(lines):
                child = lines[index].strip()
                if not child.startswith("- "):
                    break
                items.append(child[2:].strip())
                index += 1
            data[key] = items
            continue

        data[key] = parse_scalar(value)
    return data


def parse_scalar(value: str) -> object:
    value = value.strip().strip('"').strip("'")
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    return value


def derive_title(note_path: Path, frontmatter: dict[str, object], body: str) -> str:
    title = optional_str(frontmatter.get("title"))
    if title:
        return title
    match = HEADING_RE.search(body)
    if match:
        return match.group(1).strip()
    return note_path.stem.replace("-", " ").replace("_", " ").strip() or note_path.stem


def derive_summary(body: str, limit: int = 220) -> str:
    lines = [line.strip() for line in body.splitlines()]
    parts = [line for line in lines if line and not line.startswith("#")]
    if not parts:
        return ""
    summary = " ".join(parts)
    if len(summary) <= limit:
        return summary
    return summary[: limit - 3].rstrip() + "..."


def normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def normalize_tags(frontmatter_tags: object, body: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for candidate in normalize_list(frontmatter_tags):
        normalized = candidate.lstrip("#").strip()
        if normalized and normalized not in seen:
            tags.append(normalized)
            seen.add(normalized)
    for match in TAG_RE.findall(body):
        normalized = match.strip()
        if normalized and normalized not in seen:
            tags.append(normalized)
            seen.add(normalized)
    return tags


def extract_wikilinks(body: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for match in WIKILINK_RE.findall(body):
        cleaned = match.strip()
        if cleaned and cleaned not in seen:
            values.append(cleaned)
            seen.add(cleaned)
    return values


def build_locator(note_path: Path, vault_root: Path) -> str:
    try:
        return str(note_path.relative_to(vault_root))
    except ValueError:
        return note_path.name


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None
