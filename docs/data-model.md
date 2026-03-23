# Data Model

## Goal

The `RAG-DB` is the digital representation of the user's work context, projects, cases, tasks, contacts, notes, events, and imported source material.
The same internal model must support:

- manual entry
- email import
- Telegram import
- cloud AI conversation capture
- retrieval and assistant workflows
- multiple views over the same underlying records

## Main Structural Logic

The user's world is primarily organized as:

- Organization
- Team
- Project
- Case
- Task

Important nuance:

- not every record has a Team
- not every record has a Project
- Tasks can belong under a Case, but some tasks may stay standalone

## Storage Layers

### 1. Source Layer

Raw and user-owned material lives directly under `RAG-DB/`.

### 2. Application Layer

Generated and structured assistant artifacts live under:

- `RAG-DB/.rag_assistant/`

Current files:

- `manual_records.json`
- `index.json`
- `chroma/`

### 3. Development Layer

During schema and UI development, a local throwaway database can live at:

- `RAG-asszisztens/dev-rag-db/`

## Core Record Model

The current shared record type is `KnowledgeRecord`.
Every source must normalize into this shared model.

Core fields:

- `record_id`
- `title`
- `summary`
- `content`
- `source_type`
- `entity_type`
- `status`
- `organization`
- `team`
- `project`
- `case_name`
- `parent_id`
- `related_people`
- `tags`
- `relations`
- `decision_needed`
- `decision_context`
- `created_at`
- `updated_at`
- `deadline`
- `event_at`

## Entity Types

Current target entity set:

- `organization`
- `team`
- `project`
- `case`
- `task`
- `decision`
- `person`
- `event`
- `note`
- `source_item`

## Meaning Of The Main Entities

### Organization

Represents a company, institution, client organization, or other formal container.

### Team

Represents a team within an organization.

Rule:

- Team belongs to Organization

### Project

Represents a project, initiative, or workstream.

Rule:

- Project may belong to Organization and optionally to Team
- Project is not required for every record

### Case

Represents a concrete case, issue, matter, or trackable work item.

Rule:

- Cases can exist under a Project, but not necessarily
- Tasks often belong under Cases

### Task

Represents an actionable work item.

Rule:

- A Task may belong under a Case
- A Task may also remain standalone
- Task has a dedicated `deadline`

### Decision

Decision is a separate entity, not just an attribute.

But records may also carry:

- `decision_needed`
- `decision_context`

This allows both:

- marking that a record needs a decision
- storing an actual decision as its own entity

### Person

Represents a related person or contact.

Rule:

- a Person can participate in Organization, Team, Project, Case, and Event contexts
- this later helps email-to-record matching

### Event

Represents a dated event, meeting, milestone, or calendar item.

### Note

General-purpose record for thoughts, summaries, raw captures, and context notes.

### Source Item

Represents imported source material such as email, Telegram content, or cloud AI output.

## Relationship Rules

Core relationships we want to support:

- Team belongs to Organization
- Project belongs to Organization, and optionally to Team
- Case may belong to Project
- Task may belong to Case
- Person can participate in Organization, Team, Project, Case, and Event
- SourceItem may attach to an existing record or open a new record
- Decision may belong to or relate to Project, Case, or Task

Implementation note:

- `parent_id` stores the primary hierarchical parent
- `relations` store additional graph-like links

## Upsert Logic

Imported sources should support both patterns:

- if the system can identify where the information belongs, it upserts into the related record space
- if the information is new, it opens a new record

Later the user may manually reattach or reorganize the record.

This applies to:

- email
- Telegram
- cloud AI sourced knowledge

## View Behavior

All views should expose the same underlying record set.
The difference is interaction style, not data ownership.

### Table

Best for:

- overview
- sorting
- filtering
- fast scanning

### Kanban

Best for:

- status manipulation
- especially for Case and Task style records

Direct manipulation idea:

- dragging between columns changes `status`

### Timeline / Calendar

Best for:

- deadlines
- events
- temporal planning

Direct manipulation idea:

- dragging adjusts `deadline` or `event_at`

### Mindmap

Best for:

- relation editing
- structural understanding
- parent-child organization

Direct manipulation idea:

- node movement and link editing change hierarchy or relations

## Immediate Modeling Rule

Before building more importers, every new source must map into the same normalized record model.
That means:

- one shared identity model
- one shared metadata vocabulary
- one shared hierarchy model
- one shared relation model
- multiple ingestion channels
