from __future__ import annotations

from pathlib import Path

from rag_assistant.chunking import chunk_text
from rag_assistant.config import AppConfig
from rag_assistant.loader import iter_source_files, read_text_file
from rag_assistant.models import DocumentChunk, KnowledgeRecord
from rag_assistant.records import load_records


def build_index(
    source_dir: Path,
    config: AppConfig,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[DocumentChunk]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

    size = chunk_size or config.default_chunk_size
    overlap = chunk_overlap or config.default_chunk_overlap

    chunks: list[DocumentChunk] = []
    for path in iter_source_files(source_dir):
        text = read_text_file(path)
        relative_path = path.relative_to(source_dir)
        chunks.extend(chunk_text(relative_path, text, size, overlap))

    manual_records_path = config.manual_records_path_for(source_dir)
    manual_records = load_records(manual_records_path)
    for record in manual_records:
        chunks.extend(record_to_chunks(record, size, overlap))

    return chunks


def record_to_chunks(record: KnowledgeRecord, chunk_size: int, chunk_overlap: int) -> list[DocumentChunk]:
    text = record.to_search_text()
    base_chunks = chunk_text(Path(f"manual/{record.record_id}.md"), text, chunk_size, chunk_overlap)
    for chunk in base_chunks:
        chunk.title = record.title
        chunk.source_path = f"manual:{record.record_id}"
        chunk.source_type = record.source_type
        chunk.record_id = record.record_id
        chunk.entity_type = record.entity_type
        chunk.organization = record.organization or None
        chunk.team = record.team or None
        chunk.project = record.project or None
        chunk.case_name = record.case_name or None
        chunk.tags = list(record.tags)
    return base_chunks
