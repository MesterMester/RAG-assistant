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
