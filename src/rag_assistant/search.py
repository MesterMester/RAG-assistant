from __future__ import annotations

import math
import re
from collections import Counter

from rag_assistant.models import DocumentChunk


WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in WORD_RE.findall(text)]


def search_chunks(chunks: list[DocumentChunk], query: str, limit: int = 5) -> list[tuple[float, DocumentChunk]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    query_counts = Counter(query_tokens)
    results: list[tuple[float, DocumentChunk]] = []

    for chunk in chunks:
        doc_counts = Counter(tokenize(chunk.text))
        if not doc_counts:
            continue
        score = cosine_similarity(query_counts, doc_counts)
        if score > 0:
            results.append((score, chunk))

    results.sort(key=lambda item: item[0], reverse=True)
    return results[:limit]


def cosine_similarity(left: Counter, right: Counter) -> float:
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)
