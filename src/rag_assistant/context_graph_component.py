from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "context_graph",
    path=str(Path(__file__).resolve().parent / "context_graph_component"),
)


def context_graph(payload: dict, key: str = "context_graph"):
    return _COMPONENT(payload=payload, key=key, default=None)
