from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "kanban_dnd_board",
    path=str(Path(__file__).resolve().parent / "kanban_dnd_component"),
)


def kanban_dnd_board(payload: dict, key: str = "kanban_dnd_board"):
    return _COMPONENT(payload=payload, key=key, default=None)
