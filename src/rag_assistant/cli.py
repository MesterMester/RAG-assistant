from __future__ import annotations

import argparse
from pathlib import Path

from rag_assistant.config import load_config
from rag_assistant.index_store import load_index, save_index
from rag_assistant.ingest import build_index
from rag_assistant.loader import summarize_source_dir
from rag_assistant.obsidian_importer import import_obsidian_markdown
from rag_assistant.records import load_records
from rag_assistant.upserter import dry_run_upsert_batch
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

    obsidian_dry_run = subparsers.add_parser(
        "import-obsidian-dry-run",
        help="Preview what an Obsidian markdown import would propose",
    )
    obsidian_dry_run.add_argument("note_path", help="Path to a markdown note")
    obsidian_dry_run.add_argument("--source-dir", required=False, help="Path to the target RAG directory")
    obsidian_dry_run.add_argument(
        "--vault-root",
        required=False,
        help="Optional root of the Obsidian vault for relative source locators",
    )

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
        return

    if args.command == "import-obsidian-dry-run":
        source_dir = resolve_source_dir(config, args.source_dir)
        note_path = Path(args.note_path).expanduser().resolve()
        vault_root = Path(args.vault_root).expanduser().resolve() if args.vault_root else None
        batch = import_obsidian_markdown(note_path, vault_root=vault_root)
        decisions = dry_run_upsert_batch(batch, source_dir, config)
        print(f"Batch: {batch.batch_id}")
        print(f"Producer: {batch.producer}")
        print(f"Created at: {batch.created_at}")
        print(f"Changes: {len(batch.changes)}")
        for index, (change, decision) in enumerate(decisions, start=1):
            print(f"[{index}] proposed_action={decision.action} operation={change.operation}")
            if change.record:
                print(f"    title: {change.record.title}")
                print(f"    entity_type: {change.record.entity_type}")
                print(f"    summary: {change.record.summary}")
                if change.record.project:
                    print(f"    project: {change.record.project}")
                if change.record.case_name:
                    print(f"    case: {change.record.case_name}")
                if change.record.tags:
                    print(f"    tags: {', '.join(change.record.tags)}")
                if change.record.relations:
                    print(f"    relations: {', '.join(change.record.relations)}")
            if change.sources:
                for source in change.sources:
                    locator = source.locator or source.source_item_id
                    print(f"    source: {source.source_type} -> {locator}")
            if decision.matched_record_id:
                print(f"    matched_record_id: {decision.matched_record_id}")
            if decision.matched_title:
                print(f"    matched_title: {decision.matched_title}")
            print(f"    reason: {decision.reason}")
        return


if __name__ == "__main__":
    main()
