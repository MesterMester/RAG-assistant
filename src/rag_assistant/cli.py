from __future__ import annotations

import argparse
from pathlib import Path

from rag_assistant.config import load_config
from rag_assistant.index_store import load_index, save_index
from rag_assistant.ingest import build_index
from rag_assistant.loader import summarize_source_dir
from rag_assistant.records import load_records
from rag_assistant.search import search_chunks
from rag_assistant.vector_store import VectorStoreError, upsert_manual_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag", description="Local RAG assistant CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Build or refresh the local keyword index")
    ingest_parser.add_argument("--source-dir", required=False, help="Path to the source knowledge directory")
    ingest_parser.add_argument("--chunk-size", type=int, default=None)
    ingest_parser.add_argument("--chunk-overlap", type=int, default=None)

    inspect_parser = subparsers.add_parser("inspect", help="Show a safe summary of the source directory")
    inspect_parser.add_argument("--source-dir", required=False, help="Path to the source knowledge directory")

    reindex_parser = subparsers.add_parser("reindex-manual", help="Sync manual records into Chroma")
    reindex_parser.add_argument("--source-dir", required=False, help="Path to the source knowledge directory")

    search_parser = subparsers.add_parser("search", help="Search the local keyword index")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--source-dir", required=False, help="Path to the source knowledge directory")
    search_parser.add_argument("--limit", type=int, default=5)

    return parser


def resolve_source_dir(config, provided_source_dir: str | None) -> Path:
    if provided_source_dir:
        return Path(provided_source_dir).expanduser().resolve()
    if config.source_dir:
        return config.source_dir.resolve()
    raise SystemExit(
        "Source directory is not configured. Use --source-dir or set RAG_SOURCE_DIR in .env"
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()

    if args.command == "ingest":
        source_dir = resolve_source_dir(config, args.source_dir)
        index_path = config.index_path_for(source_dir)
        chunks = build_index(
            source_dir=source_dir,
            config=config,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        save_index(index_path, chunks)
        print(f"Indexed {len(chunks)} chunks into {index_path}")
        return

    if args.command == "inspect":
        source_dir = resolve_source_dir(config, args.source_dir)
        summary = summarize_source_dir(source_dir)
        records = load_records(config.manual_records_path_for(source_dir))
        print(f"Source root: {summary.root}")
        print(f"Total files: {summary.total_files}")
        print(f"Total bytes: {summary.total_bytes}")
        print(f"Manual records: {len(records)}")
        print("Files by extension:")
        if not summary.by_extension:
            print("  <no supported files found>")
        else:
            for extension, count in summary.by_extension.items():
                print(f"  {extension}: {count}")
        print(f"Manual records path: {config.manual_records_path_for(source_dir)}")
        print(f"Keyword index path: {config.index_path_for(source_dir)}")
        print(f"Chroma path: {config.chroma_dir_for(source_dir)}")
        return

    if args.command == "reindex-manual":
        source_dir = resolve_source_dir(config, args.source_dir)
        records = load_records(config.manual_records_path_for(source_dir))
        try:
            count = upsert_manual_records(records, config.chroma_dir_for(source_dir), config.ollama_embed_model)
        except VectorStoreError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Upserted {count} manual records into {config.chroma_dir_for(source_dir)}")
        return

    if args.command == "search":
        source_dir = resolve_source_dir(config, args.source_dir)
        index_path = config.index_path_for(source_dir)
        chunks = load_index(index_path)
        results = search_chunks(chunks, args.query, limit=args.limit)
        if not results:
            print("No matches found.")
            return
        for rank, (score, chunk) in enumerate(results, start=1):
            snippet = chunk.text[:220].strip()
            print(f"[{rank}] score={score:.3f} source={chunk.source_path} type={chunk.source_type}")
            print(f"    {snippet}")


if __name__ == "__main__":
    main()
