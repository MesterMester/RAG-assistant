# Importer And Upserter Contract

## Goal

The ingestion pipeline is intentionally split into two separate components:

- `Importer`
- `Upserter`

This separation is important because the system must support multiple source systems over time:

- Obsidian
- Thunderbird
- Google Drive or other Drive-like sources
- Telegram
- cloud AI conversations
- future connectors

The first concrete implementation can start with Obsidian, but the contract must already be source-agnostic.

## Component Responsibilities

### Importer

An importer is source-specific.

Its job is to:

- read and interpret the external source
- detect relevant items or changes
- transform them into a shared internal proposal format
- send a list of proposed changes to the upserter

Its job is not to write directly into the `RAG-DB`.

Examples:

- Obsidian importer reads notes, frontmatter, tags, links, and file paths
- Thunderbird importer reads emails, threads, senders, dates, and attachments
- Drive importer reads file metadata, text content, location, and sharing context

### Upserter

The upserter is source-agnostic in behavior, but source-aware in metadata.

Its job is to:

- accept normalized proposed changes from any importer or other internal workflow
- decide whether to create, update, enrich, relate, or ignore records
- apply those changes to the shared `RAG-DB`
- preserve provenance about where the information came from

The upserter should not contain source-specific parsing rules.
It may know the origin of a proposal, but only as metadata that affects provenance, trust, and later retrieval display.

## Design Rule

The importer does interpretation.
The upserter does controlled insertion and update.

That means:

- importer output must be stable and structured
- upserter input must not depend on raw Obsidian, raw email, or raw Telegram formats
- every source enters the RAG through the same proposal contract

## Provenance Principle

The system must preserve where a fact or record came from.

A record may be:

- created from a single source
- enriched by multiple sources over time
- confirmed by multiple independent sources

Examples:

- a project note first appears from Obsidian
- later the same project is confirmed by Thunderbird email traffic
- a task is mentioned in Telegram and also linked from an Obsidian note

Because of this, source information should not be a single string only.
It should support multiple provenance entries.

## Shared Proposal Model

The contract between importer and upserter should be a list of `ProposedChange` items.

Top-level shape:

```python
ProposedChangeBatch = {
    "batch_id": str,
    "producer": str,
    "created_at": str,
    "changes": list[ProposedChange],
}
```

Meaning:

- `batch_id`: identifier for one importer run
- `producer`: which component produced the batch, for example `obsidian_importer`
- `created_at`: batch timestamp
- `changes`: normalized proposed operations

## ProposedChange

Each proposed change is one candidate operation for the upserter.

Suggested shape:

```python
ProposedChange = {
    "change_id": str,
    "operation": str,
    "target": ProposedTarget,
    "record": ProposedRecord | None,
    "relations": list[ProposedRelation],
    "sources": list[SourceEvidence],
    "confidence": float | None,
    "reason": str,
}
```

## Operation Types

Initial operation values:

- `create_record`
- `update_record`
- `merge_into_record`
- `add_relation`
- `attach_source`
- `ignore`

These do not need to map one-to-one to storage writes.
They describe the importer's proposal, and the upserter decides the final action.

## ProposedTarget

This tells the upserter what the importer believes the change should apply to.

```python
ProposedTarget = {
    "record_id": str | None,
    "external_key": str | None,
    "match_hint": str | None,
}
```

Meaning:

- `record_id`: known internal target if already resolved
- `external_key`: source-side stable identifier, for example an Obsidian path or Thunderbird message id
- `match_hint`: optional text hint for matching, such as title or canonical name

## ProposedRecord

This is the normalized candidate record payload.

```python
ProposedRecord = {
    "title": str,
    "summary": str,
    "content": str,
    "entity_type": str,
    "status": str | None,
    "organization": str | None,
    "team": str | None,
    "project": str | None,
    "case_name": str | None,
    "parent_id": str | None,
    "related_people": list[str],
    "tags": list[str],
    "relations": list[str],
    "decision_needed": bool | None,
    "decision_context": str | None,
    "deadline": str | None,
    "event_at": str | None,
}
```

This intentionally mirrors the shared `KnowledgeRecord` model closely, so the upserter works with one vocabulary.

## ProposedRelation

These are explicit graph-like suggestions beside the main record payload.

```python
ProposedRelation = {
    "relation_type": str,
    "from_ref": str,
    "to_ref": str,
    "label": str | None,
}
```

Examples:

- Obsidian wikilink relation
- email message attached to a case
- Telegram message confirming a task

## SourceEvidence

This stores provenance for the proposal.

```python
SourceEvidence = {
    "source_type": str,
    "source_item_id": str,
    "source_label": str,
    "locator": str | None,
    "observed_at": str | None,
    "snippet": str | None,
    "confidence": float | None,
}
```

Examples:

- `source_type="obsidian"` with `locator="Projects/Alpha/meeting-note.md"`
- `source_type="thunderbird"` with `source_item_id="<message-id>"`
- `source_type="telegram"` with chat and message references

This is the place where the upserter remains aware of origin.
A record may accumulate multiple `SourceEvidence` items over time.

## Upserter Output Expectations

The upserter should produce or maintain:

- the normalized `KnowledgeRecord`
- provenance history for that record
- optional source-to-record mapping for stable future upserts
- relation updates

This means the record layer should eventually support not only `source_type`, but also a richer provenance structure.

## Record Provenance Direction

The current `KnowledgeRecord` model has a single `source_type` field.
That is enough for early manual records, but not enough for multi-source confirmation.

Planned direction:

- keep `source_type` as the primary or original source marker for compatibility
- add a provenance collection later, for example `sources` or `evidence`
- allow one record to be confirmed by multiple source entries

Example:

```python
record.source_type = "obsidian"
record.sources = [
    {"source_type": "obsidian", "source_item_id": "vault/path/note.md"},
    {"source_type": "thunderbird", "source_item_id": "<message-id-1>"},
    {"source_type": "telegram", "source_item_id": "chat-42:991"},
]
```

## First Implementation Plan

### Step 1

Implement the upserter contract and internal proposal types.

### Step 2

Teach the upserter to:

- create new records
- update matching records
- attach provenance entries
- preserve source-side stable identifiers for later matching

### Step 3

Build the Obsidian importer as the first adapter that emits `ProposedChangeBatch`.

### Step 4

Later add Thunderbird, Drive, Telegram, and other importers without changing the upserter contract.

## Practical Obsidian Interpretation

For the first Obsidian importer, likely source evidence values are:

- `source_type="obsidian"`
- `source_item_id`: relative note path or explicit note id from frontmatter
- `source_label`: note title
- `locator`: relative path inside the vault

This gives a stable bridge between the external note and the internal record history.

## Summary

The architecture should treat:

- `Importer` as a source-specific proposal generator
- `Upserter` as a shared, provenance-aware record writer

Obsidian is only the first importer, not the center of the design.
