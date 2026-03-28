# Phase 1 Implementation Plan

## Goal

Phase 1 introduces the minimum structural changes needed to support the new two-graph direction:

- preserve the current mindmap as the first `Context Graph`
- extend the shared record model with planning placement
- prepare the Streamlit UI for a first `Execution Graph` tab

This phase should avoid large refactors.
It should create a clean base for the first scheduling-oriented graph MVP.

## Phase 1 Scope

Included in this phase:

- shared record model extension
- backward-compatible persistence
- minimal UI exposure of planning placement
- tab naming cleanup toward `Context Graph`
- planning container vocabulary
- developer-facing documentation updates

Not included in this phase:

- full drag-and-drop execution graph UI
- typed relations
- freeform graph editing in the browser
- historical rescheduling
- global graph

## Target Outcome

At the end of Phase 1, the system should support this:

1. a record can store its logical hierarchy exactly as today
2. the same record can also store a planning placement
3. the UI can show and edit that planning placement
4. the current mindmap remains functional
5. the project has a stable container vocabulary for the future `Execution Graph`

## Data Model Changes

### Required Additions To `KnowledgeRecord`

Add the following fields:

- `planning_bucket: str = ""`
- `planning_order: int | None = None`
- `focus_rank: int | None = None`

Why these three:

- `planning_bucket` is the minimum needed for execution placement
- `planning_order` allows ordering inside a bucket without redesign later
- `focus_rank` gives a lightweight path toward a `main_focus` area

### File To Change

- [src/rag_assistant/models.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/models.py)

### Backward Compatibility Rules

In `KnowledgeRecord.from_dict()`:

- default missing `planning_bucket` to `""`
- default missing `planning_order` to `None`
- default missing `focus_rank` to `None`

This ensures older `manual_records.json` files remain readable without migration scripts.

## Persistence And Normalization Changes

### Current Situation

Persistence is already centralized enough:

- record loading and saving happens in [src/rag_assistant/records.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/records.py)
- updates happen through `upsert_record()`
- the JSON format is a direct serialization of `KnowledgeRecord`

### Needed Changes

No storage redesign is needed in Phase 1.

But `normalize_records()` should:

- trim `planning_bucket`
- normalize invalid values to `""`
- leave `planning_order` and `focus_rank` untouched unless future validation is added

### File To Change

- [src/rag_assistant/records.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/records.py)

## Planning Container Vocabulary

Phase 1 should define a single source of truth for allowed buckets.

Recommended initial buckets:

- `main_focus`
- `today`
- `tomorrow`
- `this_week`
- `next_week`
- `later`
- `parking`
- `planned_unassigned`

### Implementation Direction

Create a constant in the Streamlit layer first, then later move it into a shared module if CLI or importers need it.

Suggested constant name:

- `PLANNING_BUCKET_OPTIONS`

### File To Change

- [src/rag_assistant/streamlit_app.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/streamlit_app.py)

## UI Changes

### 1. Record Editor

The main editor should expose planning placement in a simple form first.

Recommended fields:

- `Planning bucket` selectbox
- optional `Focus rank` number input

Do not expose `planning_order` yet in the general editor unless needed.

Reason:

- the user needs immediate scheduling capability
- ordering is secondary until the execution surface exists

### 2. Detail View

The detail view should display:

- current planning bucket
- current focus rank if present

This makes the separation between context and planning visible.

### 3. Table View

The table should include at least:

- `planning_bucket`

Optional for Phase 1:

- `focus_rank`

This gives a bulk-edit fallback before drag-and-drop exists.

### 4. Mindmap Tab Label

The current `Mindmap` tab should be renamed in the UI to reflect its actual role.

Recommended new label:

- `Context Graph`

The internal helper names can remain as-is in Phase 1 if that reduces churn.

### Files To Change

- [src/rag_assistant/streamlit_app.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/streamlit_app.py)

## First Execution Graph Placeholder

Phase 1 does not need the full execution graph yet, but it should prepare the tab structure.

Recommended approach:

- add a new tab named `Execution Graph`
- render fixed planning buckets as grouped sections
- show only `task` records by default
- allow opening a record from each bucket
- optionally allow changing `planning_bucket` with a selectbox per task

This is intentionally not the final graph UI.
It is a transitional planning surface that proves the data model and workflow.

### Why This Is Worth Doing In Phase 1

- validates the planning bucket model early
- gives immediate user value
- reduces risk before investing in custom drag-and-drop behavior

## Search And Indexing Impact

Phase 1 does not require retrieval changes.

The planning metadata can stay out of search text for now.

Reason:

- planning placement is operational metadata, not core semantic content
- adding it to search could create noisy retrieval behavior

So `record.to_search_text()` should remain unchanged in Phase 1 unless a strong use case appears.

### Files Likely Unchanged

- [src/rag_assistant/ingest.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/ingest.py)
- [src/rag_assistant/search.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/search.py)

## Concrete File-Level Worklist

### 1. Model

Update [src/rag_assistant/models.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/models.py):

- add new fields to `KnowledgeRecord`
- extend `from_dict()`
- include `planning_bucket` in `to_table_row()`
- optionally include `focus_rank` in `to_table_row()`

### 2. Record Normalization

Update [src/rag_assistant/records.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/records.py):

- validate and normalize `planning_bucket`

### 3. Streamlit Editor And Views

Update [src/rag_assistant/streamlit_app.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/streamlit_app.py):

- define planning bucket constants
- add planning fields to `render_record_editor()`
- carry planning fields through all `KnowledgeRecord(...)` reconstruction sites
- include planning fields in `record_from_table_row()`
- add `Execution Graph` tab
- rename `Mindmap` tab label to `Context Graph`

### 4. Documentation

Update:

- [docs/data-model.md](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/docs/data-model.md)
- [docs/architecture.md](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/docs/architecture.md)
- [docs/graph-views.md](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/docs/graph-views.md)

Only small consistency updates should be needed because the conceptual spec already exists.

## Implementation Order

### Step 1. Model First

Add the three new fields and defaults.

Why first:

- every later UI or persistence change depends on the model

### Step 2. Propagate Through Reconstruction Sites

The current UI frequently recreates `KnowledgeRecord` objects.
Every one of those call sites must preserve the new planning fields.

This is the main regression risk in Phase 1.

### Step 3. Editor Exposure

Add planning controls to the create and edit forms.

This proves end-to-end persistence.

### Step 4. Table Support

Expose `planning_bucket` in the table editor.

This gives fast bulk editing before richer interaction exists.

### Step 5. Execution Graph Placeholder Tab

Render tasks grouped by bucket.

This gives immediate value and validates the execution-oriented perspective.

### Step 6. Rename Mindmap In The UI

Shift user language from generic `mindmap` to `Context Graph`.

## Regression Risks

### Reconstructed Record Loss

Risk:

- many existing save flows rebuild `KnowledgeRecord` manually
- new fields may accidentally be dropped when editing status, date, or mindmap links

Mitigation:

- audit every `KnowledgeRecord(...)` call in the Streamlit app
- preserve `planning_bucket`, `planning_order`, and `focus_rank` everywhere

### Table Editing Mismatch

Risk:

- `record_from_table_row()` may ignore new fields and silently erase them

Mitigation:

- include `planning_bucket` explicitly
- keep unset fields inherited from the existing record

### Old Data Compatibility

Risk:

- older stored records do not contain the new fields

Mitigation:

- rely on `from_dict()` defaults
- do not require a migration script in Phase 1

## Acceptance Criteria

Phase 1 is complete when:

1. new and existing records load correctly without migration
2. a task can be assigned to a planning bucket from the editor
3. that planning bucket persists after reload
4. editing status, dates, or relations does not erase planning placement
5. the table view can show the planning bucket
6. the UI exposes both `Context Graph` and `Execution Graph` tabs
7. the current context-oriented graph remains functional

## Recommended Phase 1.5 Follow-Up

If Phase 1 lands cleanly, the next small step should be:

- bucket-to-bucket movement with dedicated action buttons
- a compact `main_focus` panel
- clearer visual styling for planning containers

That would create a strong bridge toward full drag-and-drop.
