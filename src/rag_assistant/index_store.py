from __future__ import annotations

import json
from pathlib import Path

from rag_assistant.models import DocumentChunk


def save_index(path: Path, chunks: list[DocumentChunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "chunks": [chunk.to_dict() for chunk in chunks],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index(path: Path) -> list[DocumentChunk]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [DocumentChunk.from_dict(item) for item in payload.get("chunks", [])]
