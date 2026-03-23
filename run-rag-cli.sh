#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/attila/Programs/AI/GitHUB1/RAG-asszisztens"
cd "$PROJECT_DIR"

echo "[rag-cli] project dir: $PROJECT_DIR"
echo "[rag-cli] python: $(command -v python3)"
echo "[rag-cli] args: $*"
echo

exec env PYTHONPATH=src python3 -m rag_assistant.cli "$@"
