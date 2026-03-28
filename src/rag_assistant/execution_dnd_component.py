from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "execution_dnd_board",
    path=str(Path(__file__).resolve().parent / "execution_dnd_component"),
)


def execution_dnd_board(payload: dict, key: str = "execution_dnd_board"):
    return _COMPONENT(payload=payload, key=key, default=None)
