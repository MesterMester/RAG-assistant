#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/attila/Programs/AI/GitHUB1/RAG-asszisztens"
APP_FILE="$PROJECT_DIR/src/rag_assistant/streamlit_app.py"

cd "$PROJECT_DIR"

echo "[rag-ui] project dir: $PROJECT_DIR"
echo "[rag-ui] python: $(command -v python3)"
echo "[rag-ui] python version: $(python3 --version 2>&1)"
echo "[rag-ui] app file: $APP_FILE"
echo "[rag-ui] source dir from .env: $(grep '^RAG_SOURCE_DIR=' .env | cut -d= -f2- || true)"
echo "[rag-ui] starting Streamlit in non-interactive mode"
echo "[rag-ui] open this in your browser: http://localhost:8501"
echo "[rag-ui] press Ctrl+C to stop"
echo

exec env \
  PYTHONPATH=src \
  STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
  python3 -m streamlit run "$APP_FILE" --server.headless true --browser.gatherUsageStats false
