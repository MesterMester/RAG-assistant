from __future__ import annotations

from pathlib import Path

from rag_assistant.models import DocumentChunk


def chunk_text(
    source_path: Path,
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []

    step = max(1, chunk_size - chunk_overlap)
    chunks: list[DocumentChunk] = []
    title = source_path.stem

    for start in range(0, len(cleaned), step):
        chunk_text_value = cleaned[start : start + chunk_size].strip()
        if not chunk_text_value:
            continue
        chunk_id = f"{source_path.as_posix()}::{start}"
        chunks.append(
            DocumentChunk(
                chunk_id=chunk_id,
                source_path=source_path.as_posix(),
                title=title,
                text=chunk_text_value,
                tokens_estimate=max(1, len(chunk_text_value) // 4),
            )
        )
        if start + chunk_size >= len(cleaned):
            break

    return chunks
