# Architecture Notes

Current phase:

- local-first CLI and Streamlit workflow
- source documents are read from a user-provided directory or local `.env`
- the private `RAG-DB` is outside the Git repository
- a temporary repo-local `dev-rag-db/` can be used during schema design and UI development
- generated local artifacts are stored under `RAG-DB/.rag_assistant/`
- manual records have a shared internal schema and can be indexed together with source files
- retrieval currently combines keyword indexing and Chroma upsert for manual records

Current manual source model:

- source type: `manual`
- views: table, kanban, timeline, mindmap
- fields: title, summary, content, project, status, kind, tags, relations, due date, event date

Planned next steps:

- incremental upsert for changed files only
- dedicated email and Telegram importers
- cloud AI conversation capture as a new source type
- richer metadata and entity relations
- retrieval orchestration over keyword and vector results
- split graph views into `Context Graph` and `Execution Graph`

Importer and upserter separation:

- importers are source-specific adapters that generate normalized proposed changes
- the upserter is the shared ingestion component that applies those changes into the `RAG-DB`
- the upserter preserves source provenance, so one record may later be supported by multiple origins

See:

- `docs/importer-upserter.md`
- `docs/graph-views.md`
- `docs/phase-1-implementation-plan.md`
- `docs/context-graph-2.0-spec.md`
- `docs/context-graph-phase-a-plan.md`
