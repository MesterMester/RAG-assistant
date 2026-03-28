from __future__ import annotations

from dataclasses import replace
from datetime import date
import subprocess

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from rag_assistant.config import load_config
from rag_assistant.execution_dnd_component import execution_dnd_board
from rag_assistant.index_store import load_index, save_index
from rag_assistant.ingest import build_index
from rag_assistant.models import KnowledgeRecord
from rag_assistant.planning_layout import (
    add_block,
    add_day,
    add_week,
    day_for_bucket,
    ensure_layout,
    iter_buckets,
    layout_rows,
    remove_block,
    remove_day,
    remove_week,
    save_planning_layout,
)
from rag_assistant.records import build_record_id, delete_record, load_records, normalize_records, save_records, upsert_record
from rag_assistant.search import search_chunks
from rag_assistant.vector_store import VectorStoreError, upsert_manual_records

STATUS_OPTIONS = ["inbox", "next", "active", "waiting", "done", "cancelled", "archived"]
ENTITY_OPTIONS = [
    "organization",
    "team",
    "project",
    "case",
    "task",
    "decision",
    "person",
    "event",
    "note",
    "source_item",
]
NONE_OPTION = "<nincs>"


def record_label(record: KnowledgeRecord) -> str:
    path_bits = [bit for bit in [record.organization, record.team, record.project, record.case_name] if bit]
    suffix = f" [{' / '.join(path_bits)}]" if path_bits else ""
    return f"{record.title} ({record.entity_type}){suffix}"


def update_record(existing: KnowledgeRecord, **changes) -> KnowledgeRecord:
    return replace(existing, **changes)


def planning_bucket_titles(layout: dict) -> dict[str, str]:
    titles = {"": "Nincs utemezve"}
    for bucket in iter_buckets(layout):
        week_title = bucket.get("week_title", "").strip()
        day_title = bucket.get("day_title", "").strip()
        bucket_title = bucket.get("title", "").strip()
        titles[bucket.get("key", "")] = " / ".join(part for part in [week_title, day_title, bucket_title] if part)
    return titles


def planning_bucket_options(layout: dict, extra_values: list[str] | None = None) -> list[str]:
    options = [""]
    options.extend(bucket.get("key", "") for bucket in iter_buckets(layout) if bucket.get("key"))
    for value in extra_values or []:
        if value and value not in options:
            options.append(value)
    return options


def planning_bucket_label(value: str, titles: dict[str, str] | None = None) -> str:
    title_map = titles or {"": "Nincs utemezve"}
    return title_map.get(value or "", value or "Nincs utemezve")


def persist_planning_layout(layout: dict, layout_path) -> None:
    save_planning_layout(layout_path, layout)
    st.success("Planning layout mentve.")
    st.rerun()


def get_selected_record(records: list[KnowledgeRecord]) -> KnowledgeRecord | None:
    if not records:
        return None
    lookup = {record.record_id: record for record in records}
    selected_id = st.session_state.get("selected_record_id")
    if selected_id in lookup:
        return lookup[selected_id]
    selected = records[0]
    st.session_state["selected_record_id"] = selected.record_id
    return selected


def parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def persist_record(record: KnowledgeRecord, source_dir, config, records_path, index_path, chroma_dir) -> None:
    saved = upsert_record(records_path, record)
    st.session_state["selected_record_id"] = saved.record_id
    refreshed_records = load_records(records_path)
    chunks = build_index(source_dir, config)
    save_index(index_path, chunks)
    st.success(f"Mentve: {saved.record_id}")
    st.info(f"Keyword index frissitve: {len(chunks)} chunk")
    try:
        count = upsert_manual_records(refreshed_records, chroma_dir, config.ollama_embed_model)
        st.info(f"Chroma upsert kesz: {count} rekord.")
    except VectorStoreError as exc:
        st.warning(f"Chroma upsert kihagyva: {exc}")
    st.rerun()


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def normalize_table_value(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def persist_records_bulk(records: list[KnowledgeRecord], source_dir, config, records_path, index_path, chroma_dir, success_message: str) -> None:
    save_records(records_path, records)
    refreshed_records = load_records(records_path)
    chunks = build_index(source_dir, config)
    save_index(index_path, chunks)
    st.success(success_message)
    st.info(f"Keyword index frissitve: {len(chunks)} chunk")
    try:
        count = upsert_manual_records(refreshed_records, chroma_dir, config.ollama_embed_model)
        st.info(f"Chroma upsert kesz: {count} rekord.")
    except VectorStoreError as exc:
        st.warning(f"Chroma upsert kihagyva: {exc}")
    st.rerun()


def record_from_table_row(row: dict, existing: KnowledgeRecord) -> KnowledgeRecord:
    start_at = normalize_table_value(row.get("start_at")) or None
    due_at = normalize_table_value(row.get("due_at")) or None
    deadline = normalize_table_value(row.get("deadline")) or None
    event_at = normalize_table_value(row.get("event_at")) or None
    focus_rank_raw = normalize_table_value(row.get("focus_rank"))
    focus_rank = int(focus_rank_raw) if focus_rank_raw else None
    return update_record(
        existing,
        title=normalize_table_value(row.get("title")) or existing.title,
        summary=normalize_table_value(row.get("summary")) if "summary" in row else existing.summary,
        entity_type=normalize_table_value(row.get("entity_type")) or existing.entity_type,
        status=normalize_table_value(row.get("status")) or existing.status,
        organization=normalize_table_value(row.get("organization")),
        team=normalize_table_value(row.get("team")),
        project=normalize_table_value(row.get("project")),
        case_name=normalize_table_value(row.get("case")),
        parent_id=normalize_table_value(row.get("parent_id")),
        related_people=parse_csv_list(normalize_table_value(row.get("people"))),
        tags=parse_csv_list(normalize_table_value(row.get("tags"))),
        decision_needed=bool(row.get("decision_needed")),
        start_at=start_at,
        due_at=due_at,
        deadline=deadline,
        event_at=event_at,
        planning_bucket=normalize_table_value(row.get("planning_bucket")),
        focus_rank=focus_rank,
    )


def collect_existing_values(records: list[KnowledgeRecord]) -> dict[str, list[str]]:
    return {
        "organization": sorted(
            {record.organization for record in records if record.organization}
            | {record.title for record in records if record.entity_type == "organization" and record.title}
        ),
        "team": sorted(
            {record.team for record in records if record.team}
            | {record.title for record in records if record.entity_type == "team" and record.title}
        ),
        "project": sorted(
            {record.project for record in records if record.project}
            | {record.title for record in records if record.entity_type == "project" and record.title}
        ),
        "case_name": sorted(
            {record.case_name for record in records if record.case_name}
            | {record.title for record in records if record.entity_type == "case" and record.title}
        ),
    }


def hierarchy_from_record(record: KnowledgeRecord) -> dict[str, str]:
    return {
        "organization": record.organization or (record.title if record.entity_type == "organization" else ""),
        "team": record.team or (record.title if record.entity_type == "team" else ""),
        "project": record.project or (record.title if record.entity_type == "project" else ""),
        "case_name": record.case_name or (record.title if record.entity_type == "case" else ""),
    }


def apply_parent_hierarchy(records: list[KnowledgeRecord], parent_id: str, hierarchy_values: dict[str, str]) -> dict[str, str]:
    if not parent_id:
        return hierarchy_values
    parent_lookup = {record.record_id: record for record in records}
    parent_record = parent_lookup.get(parent_id)
    if not parent_record:
        return hierarchy_values
    derived = hierarchy_from_record(parent_record)
    for field_name in ["organization", "team", "project", "case_name"]:
        if derived[field_name]:
            hierarchy_values[field_name] = derived[field_name]
    return hierarchy_values


def hierarchy_fields_for(entity_type: str) -> list[str]:
    mapping = {
        "organization": ["organization"],
        "team": ["organization", "team"],
        "project": ["organization", "team", "project"],
        "case": ["organization", "team", "project", "case_name"],
        "task": ["organization", "team", "project", "case_name"],
        "decision": ["organization", "team", "project", "case_name"],
        "person": ["organization", "team"],
        "event": ["organization", "team", "project", "case_name"],
        "note": ["organization", "team", "project", "case_name"],
        "source_item": ["organization", "team", "project", "case_name"],
    }
    return mapping.get(entity_type, [])


def render_pick_or_create(label: str, options: list[str], default: str, key_prefix: str) -> str:
    values = [NONE_OPTION] + [value for value in options if value]
    if default and default not in values:
        values.append(default)
    default_value = default if default else NONE_OPTION
    selected = st.selectbox(
        label,
        options=values,
        index=values.index(default_value),
        key=f"{key_prefix}_{label}_select",
    )
    custom = st.text_input(
        f"{label} uj ertek",
        value="",
        placeholder="ha uj erteket szeretnel",
        key=f"{key_prefix}_{label}_custom",
    )
    return custom.strip() or ("" if selected == NONE_OPTION else selected)


def render_parent_selector(records: list[KnowledgeRecord], default: str, key_prefix: str) -> str:
    options = [NONE_OPTION] + [record.record_id for record in records]
    if default and default not in options:
        options.append(default)
    selected_default = default if default else NONE_OPTION
    selected = st.selectbox(
        "Szulo rekord",
        options=options,
        index=options.index(selected_default),
        format_func=lambda item: NONE_OPTION if item == NONE_OPTION else record_label(next((record for record in records if record.record_id == item), KnowledgeRecord(item, item, "", "", "manual", "note", "inbox"))),
        key=f"{key_prefix}_parent_select",
    )
    custom = st.text_input(
        "Szulo rekord ID uj ertek",
        value="",
        placeholder="ha nem a listabol valasztasz",
        key=f"{key_prefix}_parent_custom",
    )
    return custom.strip() or ("" if selected == NONE_OPTION else selected)


def render_relations_selector(records: list[KnowledgeRecord], default_values: list[str], key_prefix: str) -> list[str]:
    record_ids = [record.record_id for record in records]
    selected = st.multiselect(
        "Kapcsolatok",
        options=record_ids,
        default=[value for value in default_values if value in record_ids],
        format_func=lambda record_id: record_label(next(record for record in records if record.record_id == record_id)),
        key=f"{key_prefix}_relations_select",
    )
    extra = st.text_input(
        "Kapcsolatok extra ID-k",
        value=", ".join([value for value in default_values if value not in record_ids]),
        placeholder="vesszovel elvalasztva",
        key=f"{key_prefix}_relations_extra",
    )
    extras = [item.strip() for item in extra.split(",") if item.strip()]
    return selected + extras


def render_record_editor(
    key_prefix: str,
    records: list[KnowledgeRecord],
    existing_values: dict[str, list[str]],
    planning_options: list[str],
    planning_titles: dict[str, str],
    base_record: KnowledgeRecord | None = None,
) -> dict:
    record = base_record or KnowledgeRecord(
        record_id="",
        title="",
        summary="",
        content="",
        source_type="manual",
        entity_type="note",
        status="inbox",
    )

    title_col, entity_col = st.columns(2)
    title = title_col.text_input("Cim", value=record.title, key=f"{key_prefix}_title")
    entity_type = entity_col.selectbox(
        "Entitas tipus",
        ENTITY_OPTIONS,
        index=ENTITY_OPTIONS.index(record.entity_type) if record.entity_type in ENTITY_OPTIONS else 0,
        key=f"{key_prefix}_entity_type",
    )

    show_status = entity_type not in {"organization", "team", "person"}
    show_people = entity_type in {"organization", "team", "project", "case", "task", "event", "note", "source_item", "decision"}
    show_parent = entity_type not in {"organization"}
    show_schedule = entity_type in {"task", "case", "project", "decision", "note", "source_item"}
    show_event = entity_type == "event"
    show_decision = entity_type in {"project", "case", "task", "decision", "note", "source_item"}
    show_planning = entity_type in {"task", "case", "project", "decision", "note", "source_item", "event"}

    parent_status_people = st.columns(3)
    if show_parent:
        with parent_status_people[0]:
            parent_id = render_parent_selector(records, record.parent_id, key_prefix)
    else:
        parent_status_people[0].caption("Szulo rekord: nem relevans ehhez az entitashoz")
        parent_id = ""

    if show_status:
        status = parent_status_people[1].selectbox(
            "Statusz",
            STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(record.status) if record.status in STATUS_OPTIONS else 0,
            key=f"{key_prefix}_status",
        )
    else:
        parent_status_people[1].caption("Statusz: nem relevans ehhez az entitashoz")
        status = record.status if record.status else "inbox"

    if show_people:
        people_raw = parent_status_people[2].text_input(
            "Kapcsolodo emberek",
            value=", ".join(record.related_people),
            help="Vesszovel elvalasztva",
            key=f"{key_prefix}_people",
        )
    else:
        parent_status_people[2].caption("Kapcsolodo emberek: nem relevans ehhez az entitashoz")
        people_raw = ""

    visible_fields = hierarchy_fields_for(entity_type)
    hierarchy_values = {
        "organization": record.organization,
        "team": record.team,
        "project": record.project,
        "case_name": record.case_name,
    }
    hierarchy_values = apply_parent_hierarchy(records, parent_id, hierarchy_values)

    if parent_id and any(hierarchy_values.values()):
        st.caption(
            f"A szulo rekord alapjan atvett hierarchia: {hierarchy_values['organization'] or '-'} / {hierarchy_values['team'] or '-'} / {hierarchy_values['project'] or '-'} / {hierarchy_values['case_name'] or '-'}"
        )

    field_columns = st.columns(4)
    field_labels = {
        "organization": "Organization",
        "team": "Team",
        "project": "Projekt",
        "case_name": "Ugy",
    }
    for column, field_name in zip(field_columns, ["organization", "team", "project", "case_name"]):
        with column:
            if field_name in visible_fields:
                hierarchy_values[field_name] = render_pick_or_create(
                    field_labels[field_name],
                    existing_values[field_name],
                    hierarchy_values[field_name],
                    key_prefix,
                )
            else:
                st.caption(f"{field_labels[field_name]}: nem relevans ehhez az entitashoz")
                hierarchy_values[field_name] = ""

    summary = st.text_area("Rovid osszefoglalo", value=record.summary, height=100, key=f"{key_prefix}_summary")
    content = st.text_area("Reszletes tartalom", value=record.content, height=220, key=f"{key_prefix}_content")

    tag_col, relation_col = st.columns(2)
    tags_raw = tag_col.text_input(
        "Tagek",
        value=", ".join(record.tags),
        help="Vesszovel elvalasztva",
        key=f"{key_prefix}_tags",
    )
    with relation_col:
        relations = render_relations_selector(records, record.relations, key_prefix)

    date_col1, date_col2, date_col3, date_col4 = st.columns(4)
    if show_schedule:
        start_at = date_col1.date_input("Start date", value=parse_optional_date(record.start_at), key=f"{key_prefix}_start_at")
        due_at = date_col2.date_input("Due date", value=parse_optional_date(record.due_at), key=f"{key_prefix}_due_at")
        deadline = date_col3.date_input("Deadline", value=parse_optional_date(record.deadline), key=f"{key_prefix}_deadline")
    else:
        date_col1.caption("Start date: nem relevans ehhez az entitashoz")
        date_col2.caption("Due date: nem relevans ehhez az entitashoz")
        date_col3.caption("Deadline: nem relevans ehhez az entitashoz")
        start_at = None
        due_at = None
        deadline = None

    if show_event:
        event_at = date_col4.date_input("Esemeny datuma", value=parse_optional_date(record.event_at), key=f"{key_prefix}_event_at")
    else:
        date_col4.caption("Esemeny datuma: csak Event entitasnal relevans")
        event_at = None

    if show_decision:
        decision_needed = st.checkbox("Dontest igenyel", value=record.decision_needed, key=f"{key_prefix}_decision_needed")
        decision_context = st.text_input(
            "Dontesi kontextus",
            value=record.decision_context,
            disabled=not decision_needed,
            key=f"{key_prefix}_decision_context",
        )
    else:
        st.caption("Dontesi mezok: nem relevans ehhez az entitashoz")
        decision_needed = False
        decision_context = ""

    planning_col1, planning_col2 = st.columns(2)
    if show_planning:
        planning_choices = list(planning_options)
        if record.planning_bucket and record.planning_bucket not in planning_choices:
            planning_choices.append(record.planning_bucket)
        planning_bucket = planning_col1.selectbox(
            "Tervezesi hely",
            planning_choices,
            index=planning_choices.index(record.planning_bucket) if record.planning_bucket in planning_choices else 0,
            format_func=lambda value: planning_bucket_label(value, planning_titles),
            key=f"{key_prefix}_planning_bucket",
        )
        focus_rank = planning_col2.number_input(
            "Fokusz sorrend",
            min_value=1,
            step=1,
            value=record.focus_rank or 1,
            key=f"{key_prefix}_focus_rank",
        )
        focus_rank_enabled = planning_bucket == "main_focus"
        if not focus_rank_enabled:
            planning_col2.caption("A fokusz sorrend csak a Fo fokusz bucketnel relevans.")
            focus_rank_value = None
        else:
            focus_rank_value = int(focus_rank)
    else:
        planning_col1.caption("Tervezesi mezok: nem relevans ehhez az entitashoz")
        planning_col2.caption("Fokusz sorrend: nem relevans ehhez az entitashoz")
        planning_bucket = ""
        focus_rank_value = None

    return {
        "title": title.strip(),
        "entity_type": entity_type,
        "status": status,
        "organization": hierarchy_values["organization"].strip(),
        "team": hierarchy_values["team"].strip(),
        "project": hierarchy_values["project"].strip(),
        "case_name": hierarchy_values["case_name"].strip(),
        "parent_id": parent_id.strip(),
        "related_people": [item.strip() for item in people_raw.split(",") if item.strip()],
        "summary": summary.strip(),
        "content": content.strip(),
        "tags": [item.strip() for item in tags_raw.split(",") if item.strip()],
        "relations": relations,
        "decision_needed": decision_needed,
        "decision_context": decision_context.strip(),
        "start_at": start_at.isoformat() if isinstance(start_at, date) else None,
        "due_at": due_at.isoformat() if isinstance(due_at, date) else None,
        "deadline": deadline.isoformat() if isinstance(deadline, date) else None,
        "event_at": event_at.isoformat() if isinstance(event_at, date) else None,
        "planning_bucket": planning_bucket,
        "focus_rank": focus_rank_value,
    }


def build_mindmap_lines(records: list[KnowledgeRecord]) -> list[str]:
    lines = [
        "digraph G {",
        '  rankdir="LR";',
        '  graph [splines=curved, overlap=false, pad="0.2"];',
        '  node [style="filled", color="#4B5563", fontname="Helvetica", penwidth="1.2"];',
        '  edge [color="#9CA3AF", arrowsize="0.8", penwidth="1.1"];',
    ]
    nodes: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    lookup = {record.record_id: record for record in records}

    def shape_for_entity(entity_type: str) -> tuple[str, str, str]:
        mapping = {
            "organization": ("circle", "#BFDBFE", "filled"),
            "team": ("hexagon", "#67E8F9", "filled"),
            "project": ("box", "#86EFAC", "rounded,filled"),
            "task": ("rarrow", "#FDE68A", "filled"),
            "case": ("component", "#F9A8D4", "filled"),
            "decision": ("diamond", "#FDA4AF", "filled"),
            "event": ("box", "#C4B5FD", "filled"),
            "person": ("box3d", "#FFF8E7", "filled"),
            "note": ("note", "#F5F5F4", "filled"),
            "source_item": ("tab", "#CBD5E1", "filled"),
        }
        return mapping.get(entity_type, ("box", "#FDEBD0", "filled"))

    def add_node(
        node_id: str,
        label: str,
        shape: str = "box",
        fillcolor: str = "#FDEBD0",
        style: str = "filled",
    ) -> None:
        if node_id in nodes:
            return
        nodes.add(node_id)
        lines.append(
            f'  "{node_id}" [label="{label}", shape="{shape}", fillcolor="{fillcolor}", style="{style}"];'
        )

    def add_edge(source: str, target: str, label: str) -> None:
        edge = (source, target, label)
        if edge in edges:
            return
        edges.add(edge)
        lines.append(f'  "{source}" -> "{target}";')

    for record in records:
        org_node = None
        team_node = None
        project_node = None
        case_node = None

        if record.organization:
            org_node = f"org::{record.organization}"
            add_node(org_node, f"Organization\n{record.organization}", "circle", "#BFDBFE", "filled")
        if record.team:
            team_node = f"team::{record.organization}::{record.team}"
            add_node(team_node, f"Team\n{record.team}", "hexagon", "#67E8F9", "filled")
            if org_node:
                add_edge(org_node, team_node, "contains")
        if record.project:
            project_node = f"project::{record.organization}::{record.team}::{record.project}"
            add_node(project_node, f"Project\n{record.project}", "box", "#86EFAC", "rounded,filled")
            if team_node or org_node:
                add_edge(team_node or org_node, project_node, "contains")
        if record.case_name:
            case_node = f"case::{record.organization}::{record.team}::{record.project}::{record.case_name}"
            add_node(case_node, f"Case\n{record.case_name}", "component", "#F9A8D4", "filled")
            parent = project_node or team_node or org_node
            if parent:
                add_edge(parent, case_node, "contains")

        if record.entity_type == "organization" and org_node:
            record_node = org_node
        elif record.entity_type == "team" and team_node:
            record_node = team_node
        elif record.entity_type == "project" and project_node:
            record_node = project_node
        elif record.entity_type == "case" and case_node:
            record_node = case_node
        else:
            record_node = record.record_id
            record_shape, record_fill, record_style = shape_for_entity(record.entity_type)
            add_node(record_node, record.title, record_shape, record_fill, record_style)

        is_hierarchy_record = record.entity_type in {"organization", "team", "project", "case"}
        if not is_hierarchy_record:
            if record.parent_id:
                parent_record = lookup.get(record.parent_id)
                if parent_record and parent_record.entity_type == "organization":
                    parent_node = f"org::{parent_record.organization or parent_record.title}"
                elif parent_record and parent_record.entity_type == "team":
                    parent_node = f"team::{parent_record.organization}::{parent_record.team or parent_record.title}"
                elif parent_record and parent_record.entity_type == "project":
                    parent_node = f"project::{parent_record.organization}::{parent_record.team}::{parent_record.project or parent_record.title}"
                elif parent_record and parent_record.entity_type == "case":
                    parent_node = f"case::{parent_record.organization}::{parent_record.team}::{parent_record.project}::{parent_record.case_name or parent_record.title}"
                else:
                    parent_node = record.parent_id
                add_edge(parent_node, record_node, "parent")
            else:
                container_parent = case_node or project_node or team_node or org_node
                if container_parent:
                    add_edge(container_parent, record_node, "item")

        for relation in record.relations:
            add_edge(record_node, relation, "rel")

    lines.append("}")
    return lines


def render_mindmap_svg(records: list[KnowledgeRecord]) -> str | None:
    dot_input = "\n".join(build_mindmap_lines(records))
    try:
        result = subprocess.run(
            ["dot", "-Tsvg"],
            input=dot_input,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def render_interactive_mindmap(svg: str, height: int = 760) -> str:
    escaped_svg = svg.replace("`", "\`")
    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;background:#fcfcfd;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid #e5e7eb;background:#f8fafc;font-family:Helvetica,Arial,sans-serif;font-size:13px;color:#475569;">
        <span>Zoom: egérgörgő | Mozgatás: húzás | Reset: dupla kattintás</span>
      </div>
      <div id="mindmap-shell" style="width:100%;height:{height}px;overflow:hidden;cursor:grab;background:white;"></div>
    </div>
    <script>
    const shell = document.getElementById('mindmap-shell');
    shell.innerHTML = `{escaped_svg}`;
    const svg = shell.querySelector('svg');
    if (svg) {{
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', '100%');
      svg.style.width = '100%';
      svg.style.height = '100%';
      svg.style.userSelect = 'none';

      const viewBox = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
      let state = {{ x: 0, y: 0, w: 100, h: 100 }};
      if (viewBox.length === 4 && viewBox.every(v => !Number.isNaN(v))) {{
        state = {{ x: viewBox[0], y: viewBox[1], w: viewBox[2], h: viewBox[3] }};
      }} else {{
        const box = svg.getBBox();
        state = {{ x: box.x, y: box.y, w: box.width || 100, h: box.height || 100 }};
      }}
      const initial = {{ ...state }};
      const setViewBox = () => svg.setAttribute('viewBox', `${{state.x}} ${{state.y}} ${{state.w}} ${{state.h}}`);
      setViewBox();

      let dragging = false;
      let last = null;

      shell.addEventListener('mousedown', (event) => {{
        dragging = true;
        last = {{ x: event.clientX, y: event.clientY }};
        shell.style.cursor = 'grabbing';
      }});

      window.addEventListener('mouseup', () => {{
        dragging = false;
        last = null;
        shell.style.cursor = 'grab';
      }});

      window.addEventListener('mousemove', (event) => {{
        if (!dragging || !last) return;
        const rect = shell.getBoundingClientRect();
        const dx = ((event.clientX - last.x) / rect.width) * state.w;
        const dy = ((event.clientY - last.y) / rect.height) * state.h;
        state.x -= dx;
        state.y -= dy;
        last = {{ x: event.clientX, y: event.clientY }};
        setViewBox();
      }});

      shell.addEventListener('wheel', (event) => {{
        event.preventDefault();
        const rect = shell.getBoundingClientRect();
        const mx = (event.clientX - rect.left) / rect.width;
        const my = (event.clientY - rect.top) / rect.height;
        const scale = event.deltaY < 0 ? 0.9 : 1.1;
        const nextW = state.w * scale;
        const nextH = state.h * scale;
        state.x += (state.w - nextW) * mx;
        state.y += (state.h - nextH) * my;
        state.w = nextW;
        state.h = nextH;
        setViewBox();
      }}, {{ passive: false }});

      shell.addEventListener('dblclick', () => {{
        state = {{ ...initial }};
        setViewBox();
      }});
    }}
    </script>
    """


def render_execution_layout_manager(layout: dict, layout_path) -> None:
    with st.expander("Planning blokkok szerkesztese", expanded=False):
        st.caption("A planning szerkezet itt attekintheto tablazatban. Uj hetek, napok es blokkok `+` jelleggel vehetoek fel, a session-jellegu blokkok torolhetoek is.")

        rows = layout_rows(layout)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        week_col1, week_col2, week_col3 = st.columns([1, 1, 1])
        new_week_start = week_col1.date_input("Uj het kezdete", key="planning_new_week_start")
        new_week_title = week_col2.text_input("Het cimke", key="planning_new_week_title", placeholder="opcionalis")
        if week_col3.button("+ Het", key="planning_add_week"):
            persist_planning_layout(add_week(layout, new_week_start.isoformat(), new_week_title.strip() or None), layout_path)

        weeks = layout.get("weeks", [])
        if weeks:
            day_col1, day_col2, day_col3, day_col4 = st.columns([1, 1, 1, 1])
            target_week = day_col1.selectbox(
                "Cel het",
                options=[week.get("key", "") for week in weeks],
                format_func=lambda key: next(week.get("title", key) for week in weeks if week.get("key") == key),
                key="planning_target_week",
            )
            new_day_date = day_col2.date_input("Uj nap datuma", key="planning_new_day_date")
            new_day_title = day_col3.text_input("Nap neve", key="planning_new_day_title", placeholder="opcionalis")
            if day_col4.button("+ Nap", key="planning_add_day"):
                persist_planning_layout(add_day(layout, target_week, new_day_date.isoformat(), new_day_title.strip() or None), layout_path)

        all_days = [(week.get("title", ""), day.get("key", ""), day.get("title", ""), day.get("date", "")) for week in weeks for day in week.get("days", [])]
        if all_days:
            block_col1, block_col2, block_col3 = st.columns([2, 2, 1])
            target_day = block_col1.selectbox(
                "Cel nap",
                options=[day_key for _, day_key, _, _ in all_days],
                format_func=lambda key: next(f"{week_title} / {day_title} ({day_date or '-'})" for week_title, day_key, day_title, day_date in all_days if day_key == key),
                key="planning_target_day",
            )
            new_block_title = block_col2.text_input("Uj blokk neve", key="planning_new_block_title", placeholder="pl. XY meeting vagy esti blokk")
            if block_col3.button("+ Blokk", key="planning_add_block"):
                if not new_block_title.strip():
                    st.warning("Adj nevet az uj blokknak.")
                else:
                    persist_planning_layout(add_block(layout, target_day, new_block_title.strip(), "session"), layout_path)

        remove_col1, remove_col2, remove_col3 = st.columns(3)
        if weeks:
            removable_week = remove_col1.selectbox(
                "Torolheto het",
                options=[""] + [week.get("key", "") for week in weeks],
                format_func=lambda key: "" if not key else next(week.get("title", key) for week in weeks if week.get("key") == key),
                key="planning_remove_week",
            )
            if removable_week and remove_col1.button("Het torlese", key="planning_remove_week_button"):
                persist_planning_layout(remove_week(layout, removable_week), layout_path)
        if all_days:
            removable_day = remove_col2.selectbox(
                "Torolheto nap",
                options=[""] + [day_key for _, day_key, _, _ in all_days],
                format_func=lambda key: "" if not key else next(f"{week_title} / {day_title}" for week_title, day_key, day_title, _ in all_days if day_key == key),
                key="planning_remove_day",
            )
            if removable_day and remove_col2.button("Nap torlese", key="planning_remove_day_button"):
                persist_planning_layout(remove_day(layout, removable_day), layout_path)
        removable_blocks = [bucket for bucket in iter_buckets(layout) if bucket.get("lane") == "session"]
        if removable_blocks:
            removable_block = remove_col3.selectbox(
                "Torolheto blokk",
                options=[""] + [bucket.get("key", "") for bucket in removable_blocks],
                format_func=lambda key: "" if not key else next(
                    f"{bucket.get('week_title', '')} / {bucket.get('day_title', '')} / {bucket.get('title', key)}"
                    for bucket in removable_blocks
                    if bucket.get("key") == key
                ),
                key="planning_remove_block",
            )
            if removable_block and remove_col3.button("Blokk torlese", key="planning_remove_block_button"):
                persist_planning_layout(remove_block(layout, removable_block), layout_path)


def render_execution_graph(records: list[KnowledgeRecord], layout: dict, layout_path, source_dir, config, records_path, index_path, chroma_dir) -> None:
    st.subheader("Execution Graph")
    st.caption("Vizualis, drag-and-drop planning felulet het -> nap -> blokk szerkezettel. A blokkhoz tartozo nap datuma a task `due` mezojevel kerul osszhangba, a `deadline` ettol fuggetlen marad.")
    render_execution_layout_manager(layout, layout_path)

    task_records = [record for record in records if record.entity_type == "task"]
    if not task_records:
        st.info("Meg nincs task rekord az execution graphhoz.")
        return

    component_payload = {
        "weeks": layout.get("weeks", []),
        "tasks": [
            {
                "record_id": record.record_id,
                "title": record.title,
                "project": record.project,
                "case_name": record.case_name,
                "planning_bucket": record.planning_bucket,
                "focus_rank": record.focus_rank,
            }
            for record in task_records
        ],
    }

    drag_result = execution_dnd_board(component_payload, key="execution_dnd_surface")
    if isinstance(drag_result, dict) and drag_result.get("action") == "move_task":
        event_id = str(drag_result.get("event_id", "")).strip()
        if event_id and st.session_state.get("last_execution_drag_event") == event_id:
            drag_result = None
        elif event_id:
            st.session_state["last_execution_drag_event"] = event_id
    if isinstance(drag_result, dict) and drag_result.get("action") == "move_task":
        record_id = str(drag_result.get("record_id", "")).strip()
        planning_bucket = str(drag_result.get("planning_bucket", "")).strip()
        dragged_record = next((record for record in task_records if record.record_id == record_id), None)
        if dragged_record and planning_bucket and planning_bucket != dragged_record.planning_bucket:
            bucket_info = day_for_bucket(layout, planning_bucket)
            next_due = bucket_info.get("day_date") if bucket_info else dragged_record.due_at
            persist_record(
                update_record(
                    dragged_record,
                    planning_bucket=planning_bucket,
                    due_at=next_due,
                    focus_rank=None,
                ),
                source_dir,
                config,
                records_path,
                index_path,
                chroma_dir,
            )

    unscheduled_tasks = [record for record in task_records if not record.planning_bucket]
    if unscheduled_tasks:
        st.markdown("**Idopont nelkul**")
        rows = [
            {
                "title": record.title,
                "project": record.project,
                "case": record.case_name,
                "status": record.status,
                "due_at": record.due_at or "",
                "deadline": record.deadline or "",
            }
            for record in unscheduled_tasks
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        for record in unscheduled_tasks:
            if st.button(f"Megnyit: {record.title}", key=f"open_unscheduled_{record.record_id}"):
                st.session_state["selected_record_id"] = record.record_id
                st.rerun()

    known_bucket_keys = {bucket.get("key", "") for bucket in iter_buckets(layout)}
    orphan_tasks = [record for record in task_records if record.planning_bucket and record.planning_bucket not in known_bucket_keys]
    if orphan_tasks:
        st.warning("Van olyan task, ami mar nem letezo bucketbe mutat. Ezeket erdemes ujra elhelyezni.")
        for record in orphan_tasks:
            st.write(f"{record.title} -> {record.planning_bucket}")


def app() -> None:
    config = load_config()
    st.set_page_config(page_title="RAG asszisztens", layout="wide")
    st.title("RAG asszisztens")
    st.caption("Domain-modellre epulo kezi tudasbevitel, reszletnezet es szerkesztes a privat RAG-DB folott.")

    if not config.source_dir:
        st.error("A .env fajlban hianyzik a RAG_SOURCE_DIR beallitas.")
        return

    source_dir = config.source_dir.resolve()
    records_path = config.manual_records_path_for(source_dir)
    index_path = config.index_path_for(source_dir)
    chroma_dir = config.chroma_dir_for(source_dir)
    planning_layout_path = config.planning_layout_path_for(source_dir)
    records = load_records(records_path)
    planning_layout = ensure_layout(planning_layout_path)
    planning_titles = planning_bucket_titles(planning_layout)
    records, normalized_count = normalize_records(records)
    if normalized_count:
        save_records(records_path, records)
    existing_values = collect_existing_values(records)
    selected_record = get_selected_record(records)
    planning_options = planning_bucket_options(planning_layout, [record.planning_bucket for record in records])

    st.sidebar.subheader("Privat tarak")
    st.sidebar.write(f"RAG-DB: `{source_dir}`")
    st.sidebar.write(f"Manual records: `{records_path}`")
    st.sidebar.write(f"Keyword index: `{index_path}`")
    st.sidebar.write(f"Chroma: `{chroma_dir}`")
    st.sidebar.write(f"Planning layout: `{planning_layout_path}`")
    if selected_record:
        st.sidebar.subheader("Kivalasztott rekord")
        st.sidebar.write(record_label(selected_record))
        st.sidebar.caption(selected_record.record_id)

    tab_input, tab_detail, tab_table, tab_kanban, tab_timeline, tab_execution, tab_mindmap, tab_search = st.tabs(
        ["Bevitel", "Reszlet", "Tablazat", "Kanban", "Timeline", "Execution Graph", "Context Graph", "Kereses"]
    )

    with tab_input:
        st.subheader("Kezi upsert")
        create_values = render_record_editor("create", records, existing_values, planning_options, planning_titles)
        if st.button("Mentes es upsert", key="create_save"):
            if not create_values["title"]:
                st.error("A cim kotelezo.")
            else:
                record = KnowledgeRecord(
                    record_id=build_record_id(create_values["title"]),
                    title=create_values["title"],
                    summary=create_values["summary"],
                    content=create_values["content"],
                    source_type="manual",
                    entity_type=create_values["entity_type"],
                    status=create_values["status"],
                    organization=create_values["organization"],
                    team=create_values["team"],
                    project=create_values["project"],
                    case_name=create_values["case_name"],
                    parent_id=create_values["parent_id"],
                    related_people=create_values["related_people"],
                    tags=create_values["tags"],
                    relations=create_values["relations"],
                    decision_needed=create_values["decision_needed"],
                    decision_context=create_values["decision_context"],
                    start_at=create_values["start_at"],
                    due_at=create_values["due_at"],
                    deadline=create_values["deadline"],
                    event_at=create_values["event_at"],
                    planning_bucket=create_values["planning_bucket"],
                    focus_rank=create_values["focus_rank"],
                )
                persist_record(record, source_dir, config, records_path, index_path, chroma_dir)

    with tab_detail:
        st.subheader("Rekord reszlet es szerkesztes")
        if not records or not selected_record:
            st.info("Meg nincs kivalasztott rekord. Hozz letre egyet, vagy valassz egy meglevo rekordot a nezetekbol.")
        else:
            selected_id = st.selectbox(
                "Kivalasztott rekord",
                options=[record.record_id for record in records],
                index=next(index for index, record in enumerate(records) if record.record_id == selected_record.record_id),
                format_func=lambda record_id: record_label(next(record for record in records if record.record_id == record_id)),
                key="selected_record_picker",
            )
            if selected_id != st.session_state.get("selected_record_id"):
                st.session_state["selected_record_id"] = selected_id
                st.rerun()

            selected_record = next(record for record in records if record.record_id == st.session_state["selected_record_id"])
            st.caption(f"ID: {selected_record.record_id}")
            st.write(f"Letrehozva: {selected_record.created_at}")
            st.write(f"Utoljara frissitve: {selected_record.updated_at}")

            edit_values = render_record_editor(
                f"edit_{selected_record.record_id}",
                records,
                existing_values,
                planning_options,
                planning_titles,
                selected_record,
            )
            if st.button("Modositas mentese", key="edit_save"):
                updated_record = update_record(
                    selected_record,
                    title=edit_values["title"],
                    summary=edit_values["summary"],
                    content=edit_values["content"],
                    entity_type=edit_values["entity_type"],
                    status=edit_values["status"],
                    organization=edit_values["organization"],
                    team=edit_values["team"],
                    project=edit_values["project"],
                    case_name=edit_values["case_name"],
                    parent_id=edit_values["parent_id"],
                    related_people=edit_values["related_people"],
                    tags=edit_values["tags"],
                    relations=edit_values["relations"],
                    decision_needed=edit_values["decision_needed"],
                    decision_context=edit_values["decision_context"],
                    start_at=edit_values["start_at"],
                    due_at=edit_values["due_at"],
                    deadline=edit_values["deadline"],
                    event_at=edit_values["event_at"],
                    planning_bucket=edit_values["planning_bucket"],
                    focus_rank=edit_values["focus_rank"],
                )
                persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)

    with tab_table:
        st.subheader("Tablazat nezet")
        if records:
            filter_col1, filter_col2, filter_col3 = st.columns(3)
            filter_entity = filter_col1.selectbox(
                "Szures entitasra",
                [NONE_OPTION] + ENTITY_OPTIONS,
                key="table_filter_entity",
            )
            filter_status = filter_col2.selectbox(
                "Szures statuszra",
                [NONE_OPTION] + STATUS_OPTIONS,
                key="table_filter_status",
            )
            filter_text = filter_col3.text_input("Szoveges szures", key="table_filter_text")

            filtered_records = records
            if filter_entity != NONE_OPTION:
                filtered_records = [record for record in filtered_records if record.entity_type == filter_entity]
            if filter_status != NONE_OPTION:
                filtered_records = [record for record in filtered_records if record.status == filter_status]
            if filter_text.strip():
                needle = filter_text.strip().lower()
                filtered_records = [
                    record
                    for record in filtered_records
                    if needle in record.title.lower()
                    or needle in record.summary.lower()
                    or needle in record.organization.lower()
                    or needle in record.team.lower()
                    or needle in record.project.lower()
                    or needle in record.case_name.lower()
                ]

            rows = [record.to_table_row() for record in filtered_records]
            if rows:
                st.caption("A tabla itt helyben szerkesztheto. A valtozasok az Alkalmaz gombbal irhatok vissza.")
                table_df = pd.DataFrame(rows)
                edited_df = st.data_editor(
                    table_df,
                    use_container_width=True,
                    hide_index=True,
                    key="records_table_editor",
                    disabled=["record_id", "updated_at"],
                    column_config={
                        "entity_type": st.column_config.SelectboxColumn("entitas", options=ENTITY_OPTIONS, required=True),
                        "status": st.column_config.SelectboxColumn("statusz", options=STATUS_OPTIONS, required=True),
                        "decision_needed": st.column_config.CheckboxColumn("dontes?"),
                        "planning_bucket": st.column_config.SelectboxColumn("tervezesi hely", options=planning_options),
                        "record_id": st.column_config.TextColumn("record_id", disabled=True),
                        "updated_at": st.column_config.TextColumn("updated_at", disabled=True),
                    },
                )

                action_col1, action_col2 = st.columns([2, 1])
                with action_col1:
                    if st.button("Tablazat valtozasainak alkalmazasa", key="apply_table_changes"):
                        record_lookup = {record.record_id: record for record in records}
                        edited_rows = edited_df.to_dict("records")
                        updated_lookup = {}
                        changed_count = 0
                        for row in edited_rows:
                            record_id = normalize_table_value(row.get("record_id"))
                            existing = record_lookup.get(record_id)
                            if not existing:
                                continue
                            updated = record_from_table_row(row, existing)
                            if updated.to_dict() != existing.to_dict():
                                changed_count += 1
                            updated_lookup[record_id] = updated

                        merged_records = [updated_lookup.get(record.record_id, record) for record in records]
                        if changed_count == 0:
                            st.info("Nincs alkalmazando tablazatmodositas.")
                        else:
                            persist_records_bulk(
                                merged_records,
                                source_dir,
                                config,
                                records_path,
                                index_path,
                                chroma_dir,
                                f"Tablazat valtozasai mentve: {changed_count} rekord.",
                            )

                with action_col2:
                    delete_id = st.selectbox(
                        "Torlendo rekord",
                        options=[record.record_id for record in filtered_records],
                        format_func=lambda record_id: record_label(next(record for record in filtered_records if record.record_id == record_id)),
                        key="table_delete_picker",
                    )
                    if st.button("Kijelolt rekord torlese", key="delete_from_table"):
                        removed = delete_record(records_path, delete_id)
                        if not removed:
                            st.warning("A rekord mar nem talalhato.")
                        else:
                            refreshed_records = load_records(records_path)
                            persist_records_bulk(
                                refreshed_records,
                                source_dir,
                                config,
                                records_path,
                                index_path,
                                chroma_dir,
                                "Rekord torolve a tablazatnezetbol.",
                            )

                selected_id = st.selectbox(
                    "Rekord megnyitasa a tablazatbol",
                    options=[record.record_id for record in filtered_records],
                    format_func=lambda record_id: record_label(next(record for record in filtered_records if record.record_id == record_id)),
                    key="table_record_picker",
                )
                if st.button("Megnyit a reszletnezetben", key="open_from_table"):
                    st.session_state["selected_record_id"] = selected_id
                    st.rerun()
            else:
                st.info("A szurok alapjan nincs talalat.")
        else:
            st.info("Meg nincs kezzel felvitt rekord.")

    with tab_kanban:
        st.subheader("Kanban nezet")
        st.caption("Itt helyben is valthatsz statuszt a task, case es project rekordokon.")
        task_like = [record for record in records if record.entity_type in {"task", "case", "project"}]
        columns = st.columns(len(STATUS_OPTIONS))
        for column, status_name in zip(columns, STATUS_OPTIONS):
            with column:
                st.markdown(f"**{status_name}**")
                matches = [record for record in task_like if record.status == status_name]
                if not matches:
                    st.caption("Nincs elem")
                for record in matches:
                    st.markdown(f"**{record.title}**")
                    st.caption(f"{record.entity_type} | {record.organization} / {record.team} / {record.project} / {record.case_name}")
                    if record.summary:
                        st.write(record.summary)
                    if record.decision_needed:
                        st.warning("Dontest igenyel")

                    new_status = st.selectbox(
                        "Uj statusz",
                        STATUS_OPTIONS,
                        index=STATUS_OPTIONS.index(record.status) if record.status in STATUS_OPTIONS else 0,
                        key=f"kanban_status_{record.record_id}",
                        label_visibility="collapsed",
                    )
                    action_col1, action_col2 = st.columns(2)
                    with action_col1:
                        if st.button("Statusz mentese", key=f"save_kanban_{record.record_id}"):
                            updated_record = update_record(record, status=new_status)
                            persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
                    with action_col2:
                        if st.button("Megnyit", key=f"open_kanban_{record.record_id}"):
                            st.session_state["selected_record_id"] = record.record_id
                            st.rerun()
                    st.divider()

    with tab_timeline:
        st.subheader("Timeline nezet")
        st.caption("Itt helyben modosithatod a due, deadline vagy event datumot.")
        items = [record for record in records if record.due_at or record.deadline or record.event_at]
        items.sort(key=lambda item: item.event_at or item.due_at or item.deadline or "9999-99-99")
        if not items:
            st.info("Meg nincs datumhoz kotott rekord.")
        for record in items:
            when = record.event_at or record.due_at or record.deadline or ""
            label = "event" if record.event_at else ("due" if record.due_at else "deadline")
            st.markdown(f"**{when}** [{label}] {record.title}")
            st.caption(f"{record.entity_type} | {record.organization} / {record.team} / {record.project} / {record.case_name}")
            if record.summary:
                st.write(record.summary)

            edit_col1, edit_col2 = st.columns(2)
            with edit_col1:
                if record.event_at:
                    new_date = st.date_input(
                        "Uj esemeny datum",
                        value=parse_optional_date(record.event_at),
                        key=f"timeline_event_{record.record_id}",
                    )
                elif record.due_at:
                    new_date = st.date_input(
                        "Uj due date",
                        value=parse_optional_date(record.due_at),
                        key=f"timeline_due_{record.record_id}",
                    )
                else:
                    new_date = st.date_input(
                        "Uj deadline",
                        value=parse_optional_date(record.deadline),
                        key=f"timeline_deadline_{record.record_id}",
                    )
            with edit_col2:
                if st.button("Datum mentese", key=f"save_timeline_{record.record_id}"):
                    updated_record = update_record(
                        record,
                        due_at=(new_date.isoformat() if isinstance(new_date, date) else None) if record.due_at and not record.event_at else record.due_at,
                        deadline=(new_date.isoformat() if isinstance(new_date, date) else None) if record.deadline and not record.due_at and not record.event_at else record.deadline,
                        event_at=(new_date.isoformat() if isinstance(new_date, date) else None) if record.event_at else None,
                    )
                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
                if st.button("Megnyit", key=f"open_timeline_{record.record_id}"):
                    st.session_state["selected_record_id"] = record.record_id
                    st.rerun()
            st.divider()

    with tab_execution:
        render_execution_graph(records, planning_layout, planning_layout_path, source_dir, config, records_path, index_path, chroma_dir)

    with tab_mindmap:
        st.subheader("Context Graph")
        if not records:
            st.info("Meg nincs megjelenitheto rekord.")
        else:
            svg = render_mindmap_svg(records)
            if svg:
                components.html(render_interactive_mindmap(svg), height=820, scrolling=False)
            else:
                st.graphviz_chart("\n".join(build_mindmap_lines(records)), use_container_width=True)
            selected_id = st.selectbox(
                "Rekord megnyitasa a mindmapbol",
                options=[record.record_id for record in records],
                format_func=lambda record_id: record_label(next(record for record in records if record.record_id == record_id)),
                key="mindmap_record_picker",
            )
            selected_record_mindmap = next(record for record in records if record.record_id == selected_id)

            edit_col1, edit_col2 = st.columns(2)
            with edit_col1:
                new_parent = render_parent_selector(records, selected_record_mindmap.parent_id, "mindmap")
            with edit_col2:
                new_relations = render_relations_selector(records, selected_record_mindmap.relations, "mindmap")

            planning_col1, planning_col2 = st.columns(2)
            with planning_col1:
                moved_bucket = st.selectbox(
                    "Athelyezes az execution graphban",
                    planning_options,
                    index=planning_options.index(selected_record_mindmap.planning_bucket) if selected_record_mindmap.planning_bucket in planning_options else 0,
                    format_func=lambda value: planning_bucket_label(value, planning_titles),
                    key="mindmap_planning_bucket",
                )
            with planning_col2:
                moved_focus_rank = st.number_input(
                    "Fo fokusz sorrend",
                    min_value=1,
                    step=1,
                    value=selected_record_mindmap.focus_rank or 1,
                    key="mindmap_focus_rank",
                )

            button_col1, button_col2, button_col3 = st.columns(3)
            with button_col1:
                if st.button("Kapcsolatok mentese", key="save_from_mindmap"):
                    updated_record = update_record(
                        selected_record_mindmap,
                        parent_id=new_parent,
                        relations=list(new_relations),
                    )
                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
            with button_col2:
                if st.button("Athelyezes mentese", key="move_from_mindmap"):
                    updated_record = update_record(
                        selected_record_mindmap,
                        planning_bucket=moved_bucket,
                        focus_rank=int(moved_focus_rank) if moved_bucket == "main_focus" else None,
                    )
                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
            with button_col3:
                if st.button("Megnyit a reszletnezetben", key="open_from_mindmap"):
                    st.session_state["selected_record_id"] = selected_id
                    st.rerun()
            st.caption("A graf a szervezeti hierarchiat, a primary parent kapcsolatot es a tovabbi relaciokat is megjeleniti. A bucket-athelyezes itt most stabil, mentett muveletkent erheto el; a teljes vasznon beluli drag-and-drop egy kovetkezo lepeshez kulon Streamlit komponens lenne az idealis irany.")

    with tab_search:
        st.subheader("Kereses")
        query = st.text_input("Kerdes vagy kulcsszo")
        if st.button("Kereses inditasa"):
            chunks = load_index(index_path)
            results = search_chunks(chunks, query, limit=10)
            if not results:
                st.info("Nincs talalat.")
            for score, chunk in results:
                st.markdown(f"**{chunk.title}**  `score={score:.3f}`")
                st.caption(
                    f"forras: {chunk.source_path} | tipus: {chunk.source_type} | entitas: {chunk.entity_type}"
                )
                st.caption(
                    f"szervezet: {chunk.organization or '-'} | team: {chunk.team or '-'} | projekt: {chunk.project or '-'} | ugy: {chunk.case_name or '-'}"
                )
                st.write(chunk.text)
                if chunk.tags:
                    st.caption("tagek: " + ", ".join(chunk.tags))
                if chunk.record_id and st.button("Megnyit", key=f"open_search_{chunk.record_id}_{chunk.chunk_id}"):
                    st.session_state["selected_record_id"] = chunk.record_id
                    st.rerun()
                st.divider()


if __name__ == "__main__":
    app()
