from __future__ import annotations

from pathlib import Path

from rag_assistant.models import KnowledgeRecord


class VectorStoreError(RuntimeError):
    pass


def upsert_manual_records(records: list[KnowledgeRecord], persist_dir: Path, embedding_model: str) -> int:
    try:
        import chromadb
        from chromadb.config import Settings
        from langchain_ollama import OllamaEmbeddings
    except Exception as exc:
        raise VectorStoreError(f"Vector dependencies are unavailable: {exc}") from exc

    if not records:
        persist_dir.mkdir(parents=True, exist_ok=True)
        return 0

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir), settings=Settings(anonymized_telemetry=False))
    collection = client.get_or_create_collection(name="knowledge_records")
    embedder = OllamaEmbeddings(model=embedding_model)

    documents = [record.to_search_text() for record in records]
    embeddings = embedder.embed_documents(documents)
    ids = [record.record_id for record in records]
    metadatas = [
        {
            "title": record.title,
            "entity_type": record.entity_type,
            "organization": record.organization,
            "team": record.team,
            "project": record.project,
            "case_name": record.case_name,
            "status": record.status,
            "source_type": record.source_type,
            "start_at": record.start_at or "",
            "due_at": record.due_at or "",
            "deadline": record.deadline or "",
            "event_at": record.event_at or "",
            "decision_needed": str(record.decision_needed).lower(),
            "tags": ",".join(record.tags),
            "people": ",".join(record.related_people),
        }
        for record in records
    ]
    collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    return len(records)
