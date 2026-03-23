from __future__ import annotations

from datetime import date

import streamlit as st

from rag_assistant.config import load_config
from rag_assistant.index_store import load_index, save_index
from rag_assistant.ingest import build_index
from rag_assistant.models import KnowledgeRecord
from rag_assistant.records import build_record_id, load_records, upsert_record
from rag_assistant.search import search_chunks
from rag_assistant.vector_store import VectorStoreError, upsert_manual_records

STATUS_OPTIONS = ["inbox", "next", "active", "waiting", "done", "archived"]
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


def collect_existing_values(records: list[KnowledgeRecord]) -> dict[str, list[str]]:
    return {
        "organization": sorted({record.organization for record in records if record.organization}),
        "team": sorted({record.team for record in records if record.team}),
        "project": sorted({record.project for record in records if record.project}),
        "case_name": sorted({record.case_name for record in records if record.case_name}),
    }


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


def render_record_editor(key_prefix: str, records: list[KnowledgeRecord], existing_values: dict[str, list[str]], base_record: KnowledgeRecord | None = None) -> dict:
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

    visible_fields = hierarchy_fields_for(entity_type)
    hierarchy_values = {
        "organization": record.organization,
        "team": record.team,
        "project": record.project,
        "case_name": record.case_name,
    }

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

    show_status = entity_type not in {"organization", "team", "person"}
    show_people = entity_type in {"organization", "team", "project", "case", "task", "event", "note", "source_item", "decision"}
    show_parent = entity_type not in {"organization"}
    show_deadline = entity_type in {"task", "case", "project", "decision", "note", "source_item"}
    show_event = entity_type == "event"
    show_decision = entity_type in {"project", "case", "task", "decision", "note", "source_item"}

    meta_col1, meta_col2, meta_col3 = st.columns(3)
    if show_status:
        status = meta_col1.selectbox(
            "Statusz",
            STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(record.status) if record.status in STATUS_OPTIONS else 0,
            key=f"{key_prefix}_status",
        )
    else:
        meta_col1.caption("Statusz: nem relevans ehhez az entitashoz")
        status = record.status if record.status else "inbox"

    if show_parent:
        with meta_col2:
            parent_id = render_parent_selector(records, record.parent_id, key_prefix)
    else:
        meta_col2.caption("Szulo rekord: nem relevans ehhez az entitashoz")
        parent_id = ""

    if show_people:
        people_raw = meta_col3.text_input(
            "Kapcsolodo emberek",
            value=", ".join(record.related_people),
            help="Vesszovel elvalasztva",
            key=f"{key_prefix}_people",
        )
    else:
        meta_col3.caption("Kapcsolodo emberek: nem relevans ehhez az entitashoz")
        people_raw = ""

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

    date_col1, date_col2, date_col3 = st.columns(3)
    if show_deadline:
        deadline = date_col1.date_input("Deadline", value=parse_optional_date(record.deadline), key=f"{key_prefix}_deadline")
    else:
        date_col1.caption("Deadline: nem relevans ehhez az entitashoz")
        deadline = None

    if show_event:
        event_at = date_col2.date_input("Esemeny datuma", value=parse_optional_date(record.event_at), key=f"{key_prefix}_event_at")
    else:
        date_col2.caption("Esemeny datuma: csak Event entitasnal relevans")
        event_at = None

    if show_decision:
        decision_needed = date_col3.checkbox("Dontest igenyel", value=record.decision_needed, key=f"{key_prefix}_decision_needed")
        decision_context = st.text_input(
            "Dontesi kontextus",
            value=record.decision_context,
            disabled=not decision_needed,
            key=f"{key_prefix}_decision_context",
        )
    else:
        date_col3.caption("Dontesi mezok: nem relevans ehhez az entitashoz")
        decision_needed = False
        decision_context = ""

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
        "deadline": deadline.isoformat() if isinstance(deadline, date) else None,
        "event_at": event_at.isoformat() if isinstance(event_at, date) else None,
    }


def build_mindmap_lines(records: list[KnowledgeRecord]) -> list[str]:
    lines = ["digraph G {", '  rankdir="LR";', '  node [shape=box];']
    nodes: set[str] = set()
    edges: set[tuple[str, str, str]] = set()

    def add_node(node_id: str, label: str, shape: str = "box") -> None:
        if node_id in nodes:
            return
        nodes.add(node_id)
        lines.append(f'  "{node_id}" [label="{label}", shape="{shape}"];')

    def add_edge(source: str, target: str, label: str) -> None:
        edge = (source, target, label)
        if edge in edges:
            return
        edges.add(edge)
        lines.append(f'  "{source}" -> "{target}" [label="{label}"];')

    for record in records:
        org_node = None
        team_node = None
        project_node = None
        case_node = None

        if record.organization:
            org_node = f"org::{record.organization}"
            add_node(org_node, f"Org\\n{record.organization}", "folder")
        if record.team:
            team_node = f"team::{record.organization}::{record.team}"
            add_node(team_node, f"Team\\n{record.team}", "folder")
            if org_node:
                add_edge(org_node, team_node, "contains")
        if record.project:
            project_node = f"project::{record.organization}::{record.team}::{record.project}"
            add_node(project_node, f"Project\\n{record.project}", "folder")
            add_edge(team_node or org_node or project_node, project_node, "contains") if (team_node or org_node) else None
        if record.case_name:
            case_node = f"case::{record.organization}::{record.team}::{record.project}::{record.case_name}"
            add_node(case_node, f"Case\\n{record.case_name}", "folder")
            parent = project_node or team_node or org_node
            if parent:
                add_edge(parent, case_node, "contains")

        record_node = record.record_id
        add_node(record_node, f"{record.title}\\n({record.entity_type})")

        if record.parent_id:
            add_edge(record.parent_id, record_node, "parent")
        else:
            container_parent = case_node or project_node or team_node or org_node
            if container_parent:
                add_edge(container_parent, record_node, "item")

        for relation in record.relations:
            add_edge(record_node, relation, "rel")

    lines.append("}")
    return lines


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
    records = load_records(records_path)
    existing_values = collect_existing_values(records)
    selected_record = get_selected_record(records)

    st.sidebar.subheader("Privat tarak")
    st.sidebar.write(f"RAG-DB: `{source_dir}`")
    st.sidebar.write(f"Manual records: `{records_path}`")
    st.sidebar.write(f"Keyword index: `{index_path}`")
    st.sidebar.write(f"Chroma: `{chroma_dir}`")
    if selected_record:
        st.sidebar.subheader("Kivalasztott rekord")
        st.sidebar.write(record_label(selected_record))
        st.sidebar.caption(selected_record.record_id)

    tab_input, tab_detail, tab_table, tab_kanban, tab_timeline, tab_mindmap, tab_search = st.tabs(
        ["Bevitel", "Reszlet", "Tablazat", "Kanban", "Timeline", "Mindmap", "Kereses"]
    )

    with tab_input:
        st.subheader("Kezi upsert")
        create_values = render_record_editor("create", records, existing_values)
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
                    deadline=create_values["deadline"],
                    event_at=create_values["event_at"],
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

            edit_values = render_record_editor(f"edit_{selected_record.record_id}", records, existing_values, selected_record)
            if st.button("Modositas mentese", key="edit_save"):
                updated_record = KnowledgeRecord(
                    record_id=selected_record.record_id,
                    title=edit_values["title"],
                    summary=edit_values["summary"],
                    content=edit_values["content"],
                    source_type=selected_record.source_type,
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
                    created_at=selected_record.created_at,
                    deadline=edit_values["deadline"],
                    event_at=edit_values["event_at"],
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
                st.dataframe(rows, use_container_width=True)
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
                            updated_record = KnowledgeRecord(
                                record_id=record.record_id,
                                title=record.title,
                                summary=record.summary,
                                content=record.content,
                                source_type=record.source_type,
                                entity_type=record.entity_type,
                                status=new_status,
                                organization=record.organization,
                                team=record.team,
                                project=record.project,
                                case_name=record.case_name,
                                parent_id=record.parent_id,
                                related_people=list(record.related_people),
                                tags=list(record.tags),
                                relations=list(record.relations),
                                decision_needed=record.decision_needed,
                                decision_context=record.decision_context,
                                created_at=record.created_at,
                                deadline=record.deadline,
                                event_at=record.event_at,
                            )
                            persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
                    with action_col2:
                        if st.button("Megnyit", key=f"open_kanban_{record.record_id}"):
                            st.session_state["selected_record_id"] = record.record_id
                            st.rerun()
                    st.divider()

    with tab_timeline:
        st.subheader("Timeline nezet")
        st.caption("Itt helyben modosithatod a deadline-t vagy event datumot.")
        items = [record for record in records if record.deadline or record.event_at]
        items.sort(key=lambda item: item.event_at or item.deadline or "9999-99-99")
        if not items:
            st.info("Meg nincs datumhoz kotott rekord.")
        for record in items:
            when = record.event_at or record.deadline or ""
            label = "event" if record.event_at else "deadline"
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
                else:
                    new_date = st.date_input(
                        "Uj deadline",
                        value=parse_optional_date(record.deadline),
                        key=f"timeline_deadline_{record.record_id}",
                    )
            with edit_col2:
                if st.button("Datum mentese", key=f"save_timeline_{record.record_id}"):
                    updated_record = KnowledgeRecord(
                        record_id=record.record_id,
                        title=record.title,
                        summary=record.summary,
                        content=record.content,
                        source_type=record.source_type,
                        entity_type=record.entity_type,
                        status=record.status,
                        organization=record.organization,
                        team=record.team,
                        project=record.project,
                        case_name=record.case_name,
                        parent_id=record.parent_id,
                        related_people=list(record.related_people),
                        tags=list(record.tags),
                        relations=list(record.relations),
                        decision_needed=record.decision_needed,
                        decision_context=record.decision_context,
                        created_at=record.created_at,
                        deadline=None if record.event_at else (new_date.isoformat() if isinstance(new_date, date) else None),
                        event_at=(new_date.isoformat() if isinstance(new_date, date) else None) if record.event_at else None,
                    )
                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
                if st.button("Megnyit", key=f"open_timeline_{record.record_id}"):
                    st.session_state["selected_record_id"] = record.record_id
                    st.rerun()
            st.divider()

    with tab_mindmap:
        st.subheader("Mindmap nezet")
        if not records:
            st.info("Meg nincs megjelenitheto rekord.")
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

            button_col1, button_col2 = st.columns(2)
            with button_col1:
                if st.button("Kapcsolatok mentese", key="save_from_mindmap"):
                    updated_record = KnowledgeRecord(
                        record_id=selected_record_mindmap.record_id,
                        title=selected_record_mindmap.title,
                        summary=selected_record_mindmap.summary,
                        content=selected_record_mindmap.content,
                        source_type=selected_record_mindmap.source_type,
                        entity_type=selected_record_mindmap.entity_type,
                        status=selected_record_mindmap.status,
                        organization=selected_record_mindmap.organization,
                        team=selected_record_mindmap.team,
                        project=selected_record_mindmap.project,
                        case_name=selected_record_mindmap.case_name,
                        parent_id=new_parent,
                        related_people=list(selected_record_mindmap.related_people),
                        tags=list(selected_record_mindmap.tags),
                        relations=list(new_relations),
                        decision_needed=selected_record_mindmap.decision_needed,
                        decision_context=selected_record_mindmap.decision_context,
                        created_at=selected_record_mindmap.created_at,
                        deadline=selected_record_mindmap.deadline,
                        event_at=selected_record_mindmap.event_at,
                    )
                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir)
            with button_col2:
                if st.button("Megnyit a reszletnezetben", key="open_from_mindmap"):
                    st.session_state["selected_record_id"] = selected_id
                    st.rerun()
            st.caption("A graf a szervezeti hierarchiat, a primary parent kapcsolatot es a tovabbi relaciokat is megjeleniti.")

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
