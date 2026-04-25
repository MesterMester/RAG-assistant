from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "markdown_editor",
    path=str(Path(__file__).resolve().parent / "markdown_editor_component"),
)


def markdown_editor(payload: dict, key: str = "markdown_editor"):
    return _COMPONENT(payload=payload, key=key, default=payload.get("value", ""))
