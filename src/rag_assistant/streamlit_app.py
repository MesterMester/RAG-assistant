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


def app() -> None:
    config = load_config()
    st.set_page_config(page_title="RAG asszisztens", layout="wide")
    st.title("RAG asszisztens")
    st.caption("Domain-modellre epulo kezi tudasbevitel es nezetek a privat RAG-DB folott.")

    if not config.source_dir:
        st.error("A .env fajlban hianyzik a RAG_SOURCE_DIR beallitas.")
        return

    source_dir = config.source_dir.resolve()
    records_path = config.manual_records_path_for(source_dir)
    index_path = config.index_path_for(source_dir)
    chroma_dir = config.chroma_dir_for(source_dir)
    records = load_records(records_path)

    st.sidebar.subheader("Privat tarak")
    st.sidebar.write(f"RAG-DB: `{source_dir}`")
    st.sidebar.write(f"Manual records: `{records_path}`")
    st.sidebar.write(f"Keyword index: `{index_path}`")
    st.sidebar.write(f"Chroma: `{chroma_dir}`")

    tab_input, tab_table, tab_kanban, tab_timeline, tab_mindmap, tab_search = st.tabs(
        ["Bevitel", "Tablazat", "Kanban", "Timeline", "Mindmap", "Kereses"]
    )

    with tab_input:
        st.subheader("Kezi upsert")
        with st.form("manual_record_form", clear_on_submit=True):
            row1_col1, row1_col2 = st.columns(2)
            title = row1_col1.text_input("Cim")
            entity_type = row1_col2.selectbox("Entitas tipus", ENTITY_OPTIONS)

            row2_col1, row2_col2, row2_col3, row2_col4 = st.columns(4)
            organization = row2_col1.text_input("Organization")
            team = row2_col2.text_input("Team")
            project = row2_col3.text_input("Projekt")
            case_name = row2_col4.text_input("Ugy")

            row3_col1, row3_col2, row3_col3 = st.columns(3)
            status = row3_col1.selectbox("Statusz", STATUS_OPTIONS)
            parent_id = row3_col2.text_input("Szulo rekord ID")
            people_raw = row3_col3.text_input("Kapcsolodo emberek", help="Vesszovel elvalasztva")

            summary = st.text_area("Rovid osszefoglalo", height=100)
            content = st.text_area("Reszletes tartalom", height=220)

            row4_col1, row4_col2 = st.columns(2)
            tags_raw = row4_col1.text_input("Tagek", help="Vesszovel elvalasztva")
            relations_raw = row4_col2.text_input("Kapcsolatok", help="Mas rekord ID-k, vesszovel elvalasztva")

            row5_col1, row5_col2, row5_col3 = st.columns(3)
            deadline = row5_col1.date_input("Deadline", value=None)
            event_at = row5_col2.date_input("Esemeny datuma", value=None)
            decision_needed = row5_col3.checkbox("Dontest igenyel")

            decision_context = st.text_input("Dontesi kontextus", disabled=not decision_needed)
            submitted = st.form_submit_button("Mentes es upsert")

        if submitted:
            if not title.strip():
                st.error("A cim kotelezo.")
            else:
                record = KnowledgeRecord(
                    record_id=build_record_id(title),
                    title=title.strip(),
                    summary=summary.strip(),
                    content=content.strip(),
                    source_type="manual",
                    entity_type=entity_type,
                    status=status,
                    organization=organization.strip(),
                    team=team.strip(),
                    project=project.strip(),
                    case_name=case_name.strip(),
                    parent_id=parent_id.strip(),
                    related_people=[item.strip() for item in people_raw.split(",") if item.strip()],
                    tags=[item.strip() for item in tags_raw.split(",") if item.strip()],
                    relations=[item.strip() for item in relations_raw.split(",") if item.strip()],
                    decision_needed=decision_needed,
                    decision_context=decision_context.strip(),
                    deadline=deadline.isoformat() if isinstance(deadline, date) else None,
                    event_at=event_at.isoformat() if isinstance(event_at, date) else None,
                )
                saved = upsert_record(records_path, record)
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

    with tab_table:
        st.subheader("Tablazat nezet")
        rows = [record.to_table_row() for record in records]
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("Meg nincs kezzel felvitt rekord.")

    with tab_kanban:
        st.subheader("Kanban nezet")
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
                    st.divider()

    with tab_timeline:
        st.subheader("Timeline nezet")
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

    with tab_mindmap:
        st.subheader("Mindmap nezet")
        if not records:
            st.info("Meg nincs megjelenitheto rekord.")
        else:
            lines = ["digraph G {", '  rankdir="LR";']
            for record in records:
                lines.append(f'  "{record.record_id}" [label="{record.title}\\n({record.entity_type})"];')
                if record.parent_id:
                    lines.append(f'  "{record.parent_id}" -> "{record.record_id}" [label="parent"];')
                for relation in record.relations:
                    lines.append(f'  "{record.record_id}" -> "{relation}" [label="rel"];')
            lines.append("}")
            st.graphviz_chart("\n".join(lines), use_container_width=True)
            st.caption("A parent kapcsolat es a relations mezoben megadott rekord ID-k jelennek meg a grafban.")

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
                st.divider()


if __name__ == "__main__":
    app()
