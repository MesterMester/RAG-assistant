from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


APP_DIRNAME = ".rag_assistant"
INDEX_FILENAME = "index.json"
MANUAL_RECORDS_FILENAME = "manual_records.json"
CHROMA_DIRNAME = "chroma"
PLANNING_LAYOUT_FILENAME = "planning_layout.json"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


def _load_dotenv(dotenv_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(slots=True)
class AppConfig:
    project_root: Path
    source_dir: Path | None
    ollama_embed_model: str = DEFAULT_EMBED_MODEL
    default_chunk_size: int = 800
    default_chunk_overlap: int = 120

    def app_dir_for(self, source_dir: Path) -> Path:
        return source_dir / APP_DIRNAME

    def index_path_for(self, source_dir: Path) -> Path:
        return self.app_dir_for(source_dir) / INDEX_FILENAME

    def manual_records_path_for(self, source_dir: Path) -> Path:
        return self.app_dir_for(source_dir) / MANUAL_RECORDS_FILENAME

    def chroma_dir_for(self, source_dir: Path) -> Path:
        return self.app_dir_for(source_dir) / CHROMA_DIRNAME

    def planning_layout_path_for(self, source_dir: Path) -> Path:
        return self.app_dir_for(source_dir) / PLANNING_LAYOUT_FILENAME


def load_config(project_root: Path | None = None) -> AppConfig:
    root = project_root or Path(__file__).resolve().parents[2]
    env_values = _load_dotenv(root / ".env")
    source_dir_value = env_values.get("RAG_SOURCE_DIR")
    source_dir = Path(source_dir_value).expanduser() if source_dir_value else None
    ollama_embed_model = env_values.get("OLLAMA_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    return AppConfig(
        project_root=root,
        source_dir=source_dir,
        ollama_embed_model=ollama_embed_model,
    )
