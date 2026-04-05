from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from html import escape
import re
import subprocess

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from rag_assistant.config import load_config
from rag_assistant.context_graph_component import context_graph
from rag_assistant.execution_dnd_component import execution_dnd_board
from rag_assistant.history import load_events
from rag_assistant.index_store import load_index, save_index
from rag_assistant.ingest import build_index
from rag_assistant.kanban_dnd_component import kanban_dnd_board
from rag_assistant.models import KnowledgeRecord, utc_now_iso
from rag_assistant.planning_layout import (
    add_block,
    add_day,
    add_week,
    day_for_bucket,
    ensure_layout,
    find_block,
    find_day,
    iter_buckets,
    layout_rows,
    must_bucket_for_day,
    rename_week,
    rename_day,
    remove_block,
    remove_day,
    remove_week,
    save_planning_layout,
)
from rag_assistant.records import build_record_id, delete_record, load_records, normalize_records, replace_records, save_records, upsert_record
from rag_assistant.search import search_chunks
from rag_assistant.vector_store import VectorStoreError, upsert_manual_records

STATUS_OPTIONS = ["inbox", "next", "active", "waiting", "done", "cancelled", "archived"]
RELATION_TYPE_OPTIONS = ["related_to", "depends_on", "blocks", "supports", "decision_for", "references"]
ENTITY_OPTIONS = [
    "area",
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
STATUS_LABELS = {
    "inbox": "Backlog",
    "next": "Next",
    "active": "Active",
    "waiting": "Waiting",
    "done": "Done",
    "cancelled": "Cancelled",
    "archived": "Archived",
}
HIERARCHY_FIELD_BY_ENTITY = {
    "organization": "organization",
    "team": "team",
    "project": "project",
    "case": "case_name",
}


def record_label(record: KnowledgeRecord) -> str:
    path_bits = [bit for bit in [record.organization, record.team, record.project, record.case_name] if bit]
    suffix = f" [{' / '.join(path_bits)}]" if path_bits else ""
    return f"{record.title} ({record.entity_type}){suffix}"


def normalize_graph_edges(graph_edges: list[dict] | None, fallback_relations: list[str] | None = None, self_id: str = "") -> list[dict]:
    normalized: list[dict] = []
    for item in graph_edges or []:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("target_id", "")).strip()
        relation_type = str(item.get("relation_type", "")).strip() or "related_to"
        label = str(item.get("label", "")).strip()
        if not target_id or target_id == self_id:
            continue
        normalized.append({"target_id": target_id, "relation_type": relation_type, "label": label})
    if not normalized:
        normalized = [
            {"target_id": relation_id.strip(), "relation_type": "related_to", "label": ""}
            for relation_id in (fallback_relations or [])
            if relation_id.strip() and relation_id.strip() != self_id
        ]
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for item in normalized:
        edge_key = (item["target_id"], item["relation_type"], item["label"])
        if edge_key in seen:
            continue
        seen.add(edge_key)
        deduped.append(item)
    return deduped


def graph_edges_to_relations(graph_edges: list[dict]) -> list[str]:
    relations: list[str] = []
    seen: set[str] = set()
    for item in graph_edges:
        target_id = str(item.get("target_id", "")).strip()
        if target_id and target_id not in seen:
            seen.add(target_id)
            relations.append(target_id)
    return relations


def reconcile_graph_edges_with_relations(existing_edges: list[dict], relation_ids: list[str], self_id: str = "") -> list[dict]:
    preserved = [item for item in normalize_graph_edges(existing_edges, self_id=self_id) if item.get("relation_type") != "related_to"]
    related_edges = [
        {"target_id": relation_id, "relation_type": "related_to", "label": ""}
        for relation_id in relation_ids
        if relation_id and relation_id != self_id
    ]
    return normalize_graph_edges(preserved + related_edges, self_id=self_id)


def update_record(existing: KnowledgeRecord, **changes) -> KnowledgeRecord:
    return replace(existing, **changes)


def hierarchy_field_for_entity(entity_type: str) -> str | None:
    return HIERARCHY_FIELD_BY_ENTITY.get(entity_type)


def with_synced_hierarchy_title(record: KnowledgeRecord) -> KnowledgeRecord:
    field_name = hierarchy_field_for_entity(record.entity_type)
    if not field_name:
        return record
    title = record.title.strip()
    if getattr(record, field_name) == title:
        return record
    return update_record(record, **{field_name: title})


def sync_hierarchy_renames(records: list[KnowledgeRecord], previous_lookup: dict[str, KnowledgeRecord] | None = None) -> list[KnowledgeRecord]:
    synced_lookup = {record.record_id: with_synced_hierarchy_title(record) for record in records}
    if not previous_lookup:
        return [synced_lookup[record.record_id] for record in records]

    rename_order = ["organization", "team", "project", "case"]
    for entity_type in rename_order:
        for record_id, previous_record in previous_lookup.items():
            current_record = synced_lookup.get(record_id)
            if not current_record:
                continue
            if previous_record.entity_type != entity_type or current_record.entity_type != entity_type:
                continue

            field_name = hierarchy_field_for_entity(entity_type)
            if not field_name:
                continue

            old_value = getattr(previous_record, field_name).strip() or previous_record.title.strip()
            new_value = getattr(current_record, field_name).strip() or current_record.title.strip()
            if not old_value or old_value == new_value:
                continue

            for other_id, other_record in list(synced_lookup.items()):
                changes: dict[str, str] = {}
                if entity_type == "organization":
                    if other_record.organization == old_value:
                        changes["organization"] = new_value
                elif entity_type == "team":
                    if other_record.organization == current_record.organization and other_record.team == old_value:
                        changes["team"] = new_value
                elif entity_type == "project":
                    if (
                        other_record.organization == current_record.organization
                        and other_record.team == current_record.team
                        and other_record.project == old_value
                    ):
                        changes["project"] = new_value
                elif entity_type == "case":
                    if (
                        other_record.organization == current_record.organization
                        and other_record.team == current_record.team
                        and other_record.project == current_record.project
                        and other_record.case_name == old_value
                    ):
                        changes["case_name"] = new_value

                if changes:
                    if other_id != record_id:
                        changes["updated_at"] = utc_now_iso()
                    synced_lookup[other_id] = update_record(other_record, **changes)

    ordered_records = [synced_lookup[record.record_id] for record in records]
    return [with_synced_hierarchy_title(record) for record in ordered_records]


def planning_bucket_titles(layout: dict) -> dict[str, str]:
    titles = {"": "Nincs utemezve", "main_focus": "Fő fókusz most"}
    for bucket in iter_buckets(layout):
        week_title = bucket.get("week_title", "").strip()
        day_title = bucket.get("day_title", "").strip()
        bucket_title = bucket.get("title", "").strip()
        titles[bucket.get("key", "")] = " / ".join(part for part in [week_title, day_title, bucket_title] if part)
    return titles


def planning_bucket_options(layout: dict, extra_values: list[str] | None = None) -> list[str]:
    options = ["", "main_focus"]
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


def persist_layout_and_records(
    layout: dict,
    layout_path,
    records: list[KnowledgeRecord],
    source_dir,
    config,
    records_path,
    index_path,
    chroma_dir,
    history_path,
    success_message: str,
) -> None:
    save_planning_layout(layout_path, layout)
    persist_records_bulk(records, source_dir, config, records_path, index_path, chroma_dir, history_path, success_message)


def day_display_title(day_date: date, today: date) -> str:
    weekday_titles = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]
    if day_date == today:
        return "Ma"
    if day_date == today + timedelta(days=1):
        return "Holnap"
    if day_date == today + timedelta(days=2):
        return "Holnapután"
    return weekday_titles[day_date.weekday()]


def build_execution_sections(layout: dict) -> list[dict]:
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    current_week_end = current_week_start + timedelta(days=6)
    next_week_start = current_week_start + timedelta(days=7)
    next_week_end = next_week_start + timedelta(days=6)

    day_lookup: dict[str, dict] = {}
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            day_date_raw = day.get("date")
            if not day_date_raw:
                continue
            display_day = dict(day)
            day_date = date.fromisoformat(day_date_raw)
            display_day["date_obj"] = day_date
            display_day["week_start_date"] = week.get("start_date")
            if not display_day.get("custom_title"):
                display_day["title"] = day_display_title(day_date, today)
            existing = day_lookup.get(day_date_raw)
            if existing is None:
                day_lookup[day_date_raw] = display_day
                continue
            existing_week_start = existing.get("week_start_date") or ""
            current_week_start_value = week.get("start_date") or ""
            if not existing_week_start and current_week_start_value:
                day_lookup[day_date_raw] = display_day

    all_days = sorted(day_lookup.values(), key=lambda item: item["date_obj"])

    def serialize_day(day: dict) -> dict:
        serialized_day = dict(day)
        serialized_day.pop("date_obj", None)
        return serialized_day

    def make_group(key: str, title: str, days: list[dict], meta: str = "", week_key: str = "") -> dict:
        return {"key": key, "title": title, "meta": meta, "days": [serialize_day(day) for day in days], "week_key": week_key}

    def make_archive_group(key: str, title: str, days: list[dict], meta: str = "") -> dict:
        alias_keys: list[str] = []
        for day in days:
            for block in day.get("blocks", []):
                block_key = block.get("key", "")
                if block_key:
                    alias_keys.append(block_key)
        archive_day = {
            "key": f"{key}-archive",
            "title": title,
            "date": "",
            "blocks": [
                {
                    "key": f"{key}-archive-bucket",
                    "title": title,
                    "lane": "session",
                    "alias_keys": alias_keys,
                    "droppable": False,
                    "sort": "date_desc",
                    "show_due_meta": True,
                }
            ],
        }
        return {"key": key, "title": title, "meta": meta, "days": [archive_day]}

    def make_week_group(week_start: date, days: list[dict], title: str = "", week_key: str = "") -> dict:
        week_no = week_start.isocalendar().week
        week_end = week_start + timedelta(days=6)
        return make_group(
            f"week-{week_start.isoformat()}",
            title or f"{week_no}. hét",
            days,
            f"{week_start.strftime('%m.%d')} - {week_end.strftime('%m.%d')}",
            week_key=week_key,
        )

    def make_simple_week(day_items: list[dict], week_key: str, week_title: str, week_start: date) -> dict | None:
        must_aliases: list[str] = []
        prefer_aliases: list[str] = []
        for day in day_items:
            for block in day.get("blocks", []):
                lane = block.get("lane", "session")
                block_key = block.get("key", "")
                if lane == "must" and block_key:
                    must_aliases.append(block_key)
                elif lane == "prefer" and block_key:
                    prefer_aliases.append(block_key)
        if not must_aliases and not prefer_aliases:
            return None
        pseudo_day = {
            "key": f"{week_key}-summary",
            "title": week_title,
            "date": week_start.isoformat(),
            "blocks": [
                {
                    "key": f"{week_key}-must-summary",
                    "title": "Mindenképp",
                    "lane": "must",
                    "alias_keys": must_aliases,
                    "drop_target_key": must_aliases[0] if must_aliases else "",
                },
                {
                    "key": f"{week_key}-prefer-summary",
                    "title": "Lehetőleg",
                    "lane": "prefer",
                    "alias_keys": prefer_aliases,
                    "drop_target_key": prefer_aliases[0] if prefer_aliases else "",
                },
            ],
        }
        return make_week_group(week_start, [pseudo_day], title=week_title, week_key=week_key)

    def merge_weekend(days: list[dict]) -> list[dict]:
        merged: list[dict] = []
        weekend_days = [day for day in days if day["date_obj"].weekday() >= 5]
        normal_days = [day for day in days if day["date_obj"].weekday() < 5]
        merged.extend(normal_days)
        if weekend_days:
            saturday = min(weekend_days, key=lambda item: item["date_obj"])
            must_aliases: list[str] = []
            prefer_aliases: list[str] = []
            session_blocks: list[dict] = []
            for day in weekend_days:
                for block in day.get("blocks", []):
                    lane = block.get("lane", "session")
                    if lane == "must":
                        must_aliases.append(block.get("key", ""))
                    elif lane == "prefer":
                        prefer_aliases.append(block.get("key", ""))
                    elif lane == "focus":
                        continue
                    else:
                        merged_block = dict(block)
                        merged_block["title"] = f"{day.get('title', '')} - {block.get('title', '')}"
                        merged_block["alias_keys"] = [block.get("key", "")]
                        session_blocks.append(merged_block)
            blocks = [
                {"key": f"{saturday['key']}-weekend-must", "title": "Mindenképp", "lane": "must", "alias_keys": [key for key in must_aliases if key], "drop_target_key": must_aliases[0] if must_aliases else ""},
                {"key": f"{saturday['key']}-weekend-prefer", "title": "Lehetőleg", "lane": "prefer", "alias_keys": [key for key in prefer_aliases if key], "drop_target_key": prefer_aliases[0] if prefer_aliases else ""},
                *session_blocks,
            ]
            merged.append({
                "key": f"weekend-{'-'.join(day['key'] for day in weekend_days)}",
                "title": "Hétvége",
                "date": weekend_days[0]["date"],
                "blocks": blocks,
                "date_obj": weekend_days[0]["date_obj"],
            })
        return merged

    def clone_day(day: dict, title: str | None = None, include_lanes: set[str] | None = None, exclude_lanes: set[str] | None = None, key_suffix: str = "view") -> dict | None:
        blocks = []
        for block in day.get("blocks", []):
            lane = block.get("lane", "session")
            if include_lanes is not None and lane not in include_lanes:
                continue
            if exclude_lanes is not None and lane in exclude_lanes:
                continue
            blocks.append(dict(block))
        if not blocks:
            return None
        cloned = dict(day)
        cloned["key"] = f"{day.get('key', 'day')}-{key_suffix}"
        if title is not None:
            cloned["title"] = title
        cloned["blocks"] = blocks
        return cloned

    today_day = next((day for day in all_days if day["date_obj"] == today), None)
    main_focus_group_days: list[dict] = []
    today_group_days: list[dict] = []
    if today_day:
        focus_day = clone_day(today_day, title="Fő fókusz most", include_lanes={"focus"}, key_suffix="focus")
        if focus_day:
            for block in focus_day.get("blocks", []):
                if block.get("lane") == "focus":
                    original_key = block.get("key", "")
                    block["alias_keys"] = [key for key in [original_key, "main_focus"] if key]
                    block["drop_target_key"] = "main_focus"
                    block["key"] = "main_focus"
            main_focus_group_days.append(focus_day)
        main_day = clone_day(today_day, title="Ma", exclude_lanes={"focus"}, key_suffix="today")
        if main_day:
            today_group_days.append(main_day)

    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    past_yesterday = [dict(day, title="Tegnap") for day in all_days if day["date_obj"] == yesterday]
    past_day_before = [dict(day, title="Tegnapelőtt") for day in all_days if day["date_obj"] == day_before]
    past_this_week = [day for day in all_days if current_week_start <= day["date_obj"] < day_before]
    older_past = [day for day in all_days if day["date_obj"] < current_week_start]

    future_tomorrow = [dict(day, title="Holnap") for day in all_days if day["date_obj"] == today + timedelta(days=1)]
    future_this_week = [day for day in all_days if today + timedelta(days=2) <= day["date_obj"] <= current_week_end]
    future_next_week = [day for day in all_days if next_week_start <= day["date_obj"] <= next_week_end and day["date_obj"] > today + timedelta(days=1)]
    future_later_weeks: list[dict] = []
    for week in layout.get("weeks", []):
        week_start_raw = week.get("start_date")
        if not week_start_raw:
            continue
        week_start = date.fromisoformat(week_start_raw)
        if week_start <= next_week_start:
            continue
        week_days = [day for day in all_days if day.get("week_start_date") == week_start_raw]
        week_title = f"{week_start.isocalendar().week}. hét"
        simple_week = make_simple_week(week_days, week.get("key", f"week-{week_start_raw}"), week_title, week_start)
        if simple_week:
            future_later_weeks.append(simple_week)

    sections: list[dict] = [
        {"key": "main_focus", "title": "Fő fókusz most", "groups": [make_group("main_focus", "Fő fókusz most", main_focus_group_days)]},
        {"key": "today", "title": "Ma", "groups": [make_group("today", "Ma", today_group_days)]},
        {
            "key": "past",
            "title": "Múlt",
            "groups": [
                make_group("yesterday", "Tegnap", past_yesterday),
                make_group("day_before", "Tegnapelőtt", past_day_before),
                make_archive_group("earlier_this_week", "Korábban a héten", past_this_week, "Legújabb felül"),
                make_archive_group("older", "Régebb", older_past, "Legújabb felül"),
            ],
        },
        {
            "key": "future",
            "title": "Jövő",
            "groups": [
                make_group("tomorrow", "Holnap", future_tomorrow),
                make_group("this_week", "A héten", merge_weekend(future_this_week)),
                make_group("next_week", "Jövő hét", merge_weekend(future_next_week)),
                *future_later_weeks,
            ],
        },
        {"key": "unscheduled", "title": "Ütemezés nélkül", "groups": [make_group("unscheduled", "Ütemezés nélkül", [])]},
    ]
    return sections


def filter_execution_sections_by_content(sections: list[dict], tasks: list[KnowledgeRecord], show_only_with_content: bool) -> list[dict]:
    if not show_only_with_content:
        return sections

    task_counts: dict[str, int] = {}
    for record in tasks:
        bucket_key = record.planning_bucket or "__unscheduled__"
        task_counts[bucket_key] = task_counts.get(bucket_key, 0) + 1

    filtered_sections: list[dict] = []
    for section in sections:
        if section.get("key") == "unscheduled":
            unscheduled_count = task_counts.get("__unscheduled__", 0) + task_counts.get("", 0)
            if unscheduled_count > 0:
                filtered_sections.append(section)
            continue
        next_section = dict(section)
        next_groups: list[dict] = []
        for group in section.get("groups", []):
            next_group = dict(group)
            next_days: list[dict] = []
            for day in group.get("days", []):
                next_day = dict(day)
                kept_blocks: list[dict] = []
                for block in day.get("blocks", []):
                    source_keys = [key for key in (block.get("alias_keys") or [block.get("key", "")]) if key]
                    if any(task_counts.get(key, 0) > 0 for key in source_keys):
                        kept_blocks.append(dict(block))
                if kept_blocks:
                    next_day["blocks"] = kept_blocks
                    next_days.append(next_day)
            if next_days:
                next_group["days"] = next_days
                next_groups.append(next_group)
        if next_groups:
            next_section["groups"] = next_groups
            filtered_sections.append(next_section)
    return filtered_sections


def monday_of(day_value: date) -> date:
    return day_value - timedelta(days=day_value.weekday())


def month_start(day_value: date) -> date:
    return date(day_value.year, day_value.month, 1)


def month_add(month_value: date) -> date:
    year = month_value.year + (1 if month_value.month == 12 else 0)
    month = 1 if month_value.month == 12 else month_value.month + 1
    return date(year, month, 1)


def gantt_units(records: list[KnowledgeRecord], scale: str) -> list[date]:
    dates: list[date] = []
    for record in records:
        for value in [record.start_at, record.due_at, record.deadline]:
            if value:
                dates.append(date.fromisoformat(value))
    today = date.today()
    start = min(dates) if dates else today
    end = max(dates) if dates else today
    start = min(start, today - timedelta(days=14))
    end = max(end, today + timedelta(days=183))
    if scale == "day":
        current = start
        values: list[date] = []
        while current <= end:
            values.append(current)
            current += timedelta(days=1)
        return values
    if scale == "week":
        current = monday_of(start)
        end_week = monday_of(end)
        values = []
        while current <= end_week:
            values.append(current)
            current += timedelta(days=7)
        return values
    current = month_start(start)
    end_month = month_start(end)
    values = []
    while current <= end_month:
        values.append(current)
        current = month_add(current)
    return values


def gantt_index_for(day_value: date | None, units: list[date], scale: str) -> int | None:
    if day_value is None or not units:
        return None
    if scale == "day":
        try:
            return units.index(day_value)
        except ValueError:
            return None
    if scale == "week":
        target = monday_of(day_value)
    else:
        target = month_start(day_value)
    try:
        return units.index(target)
    except ValueError:
        return None


def gantt_headers(units: list[date], scale: str) -> list[str]:
    if scale == "day":
        return [value.strftime("%m.%d") for value in units]
    if scale == "week":
        return [f"{value.isocalendar().week}. hét" for value in units]
    return [value.strftime("%Y.%m") for value in units]


def matches_presence_filter(value: str | None, mode: str) -> bool:
    has_value = bool((value or "").strip())
    if mode == "has":
        return has_value
    if mode == "missing":
        return not has_value
    return True


def render_gantt_view(records: list[KnowledgeRecord]) -> None:
    st.subheader("GANTT")
    gantt_records = [record for record in records if record.entity_type in {"area", "organization", "team", "project", "case", "task"}]
    if not gantt_records:
        st.info("Meg nincs area / organization / team / project / case / task rekord a GANTT nezethez.")
        return

    with st.expander("GANTT szűrők", expanded=True):
        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        presence_options = ["all", "has", "missing"]
        presence_labels = {"all": "Mindegy", "has": "Van", "missing": "Nincs"}
        gantt_filter_start = filter_col1.selectbox(
            "Start mező",
            options=presence_options,
            format_func=lambda value: presence_labels[value],
            key="gantt_filter_start",
        )
        gantt_filter_due = filter_col2.selectbox(
            "Due mező",
            options=presence_options,
            format_func=lambda value: presence_labels[value],
            key="gantt_filter_due",
        )
        gantt_filter_deadline = filter_col3.selectbox(
            "Deadline mező",
            options=presence_options,
            format_func=lambda value: presence_labels[value],
            key="gantt_filter_deadline",
        )
        gantt_filter_relations = filter_col4.selectbox(
            "Kapcsolatok",
            options=presence_options,
            format_func=lambda value: presence_labels[value],
            key="gantt_filter_relations",
        )
        text_col1, text_col2 = st.columns(2)
        gantt_filter_text = text_col1.text_input("Szöveges szűrés", key="gantt_filter_text")
        gantt_filter_entity = text_col2.multiselect(
            "Típus",
            options=["area", "organization", "team", "project", "case", "task"],
            default=st.session_state.get("gantt_filter_entity", ["area", "organization", "team", "project", "case", "task"]),
            key="gantt_filter_entity",
        )

    scale = st.selectbox(
        "Időegység",
        options=["day", "week", "month"],
        format_func=lambda value: {"day": "Napok", "week": "Hetek", "month": "Hónapok"}.get(value, value),
        key="gantt_scale",
    )

    def relation_summary(record: KnowledgeRecord) -> str:
        edges = normalize_graph_edges(record.graph_edges, record.relations, record.record_id)
        return ", ".join(sorted({edge.get("relation_type", "related_to") for edge in edges})) or "-"

    lookup_all = {record.record_id: record for record in gantt_records}
    matched_records = [
        record
        for record in gantt_records
        if (not gantt_filter_entity or record.entity_type in gantt_filter_entity)
        if matches_presence_filter(record.start_at, gantt_filter_start)
        and matches_presence_filter(record.due_at, gantt_filter_due)
        and matches_presence_filter(record.deadline, gantt_filter_deadline)
        and matches_presence_filter("" if relation_summary(record) == "-" else relation_summary(record), gantt_filter_relations)
        and (
            not gantt_filter_text.strip()
            or gantt_filter_text.strip().lower() in record.title.lower()
            or gantt_filter_text.strip().lower() in record.summary.lower()
            or gantt_filter_text.strip().lower() in record.organization.lower()
            or gantt_filter_text.strip().lower() in record.team.lower()
            or gantt_filter_text.strip().lower() in record.project.lower()
            or gantt_filter_text.strip().lower() in record.case_name.lower()
        )
    ]
    if any(mode != "all" for mode in [gantt_filter_start, gantt_filter_due, gantt_filter_deadline, gantt_filter_relations]):
        included_ids = {record.record_id for record in matched_records}
        for record in list(matched_records):
            parent_id = record.parent_id
            while parent_id and parent_id in lookup_all and parent_id not in included_ids:
                included_ids.add(parent_id)
                parent_id = lookup_all[parent_id].parent_id
        visible_records = [record for record in gantt_records if record.record_id in included_ids]
    else:
        visible_records = gantt_records

    if not visible_records:
        st.info("A jelenlegi GANTT-szűrésre nincs találat.")
        return

    units = gantt_units(visible_records, scale)
    headers = gantt_headers(units, scale)
    column_width = 44 if scale == "day" else 64

    lookup = {record.record_id: record for record in visible_records}
    children_by_parent: dict[str, list[KnowledgeRecord]] = {}
    roots: list[KnowledgeRecord] = []
    entity_order = {"area": 0, "organization": 1, "team": 2, "project": 3, "case": 4, "task": 5}

    for record in visible_records:
        parent = lookup.get(record.parent_id) if record.parent_id else None
        if parent:
            children_by_parent.setdefault(parent.record_id, []).append(record)
        else:
            roots.append(record)

    def sort_records(items: list[KnowledgeRecord]) -> list[KnowledgeRecord]:
        return sorted(items, key=lambda item: (entity_order.get(item.entity_type, 99), item.title.lower()))

    weekend_overlay_html = "".join(
        f'<div class="gantt-weekend-bg" style="left:{index * column_width}px;width:{column_width}px;"></div>'
        for index, value in enumerate(units)
        if scale == "day" and value.weekday() >= 5
    )

    def marker_html(record: KnowledgeRecord) -> str:
        start_idx = gantt_index_for(parse_optional_date(record.start_at), units, scale)
        due_idx = gantt_index_for(parse_optional_date(record.due_at), units, scale)
        deadline_idx = gantt_index_for(parse_optional_date(record.deadline), units, scale)
        left_idx = min(value for value in [start_idx, due_idx, deadline_idx] if value is not None) if any(value is not None for value in [start_idx, due_idx, deadline_idx]) else None
        right_idx = max(value for value in [start_idx, due_idx, deadline_idx] if value is not None) if any(value is not None for value in [start_idx, due_idx, deadline_idx]) else None
        pieces: list[str] = []
        if left_idx is not None and right_idx is not None and right_idx > left_idx:
            pieces.append(
                f'<div class="gantt-span" style="left:{left_idx * column_width + 8}px;width:{(right_idx - left_idx + 1) * column_width - 16}px;"></div>'
            )
        if start_idx is not None:
            pieces.append(f'<div class="gantt-mark start" style="left:{start_idx * column_width + column_width / 2 - 10}px;">&#9654;</div>')
        if due_idx is not None:
            pieces.append(f'<div class="gantt-mark due" style="left:{due_idx * column_width + column_width / 2 - 10}px;">&#128205;</div>')
        if deadline_idx is not None:
            pieces.append(f'<div class="gantt-mark deadline" style="left:{deadline_idx * column_width + column_width / 2 - 10}px;">&#9664;</div>')
        return "".join(pieces)

    def row_html(record: KnowledgeRecord, level: int) -> str:
        return f"""
        <div class="gantt-row level-{level}">
          <div class="gantt-cell gantt-name">{escape(record.title)}</div>
          <div class="gantt-cell">{escape(record.entity_type)}</div>
          <div class="gantt-cell">{escape(record.start_at or "-")}</div>
          <div class="gantt-cell">{escape(record.due_at or "-")}</div>
          <div class="gantt-cell">{escape(record.deadline or "-")}</div>
          <div class="gantt-cell">{escape(relation_summary(record))}</div>
          <div class="gantt-timeline" style="width:{len(units) * column_width}px;">{weekend_overlay_html}{marker_html(record)}</div>
        </div>
        """

    body_rows: list[str] = []

    def render_tree(record: KnowledgeRecord, level: int) -> str:
        children = sort_records(children_by_parent.get(record.record_id, []))
        if not children:
            return row_html(record, level)
        children_html = "".join(render_tree(child, level + 1) for child in children)
        return f'<details class="gantt-details" open><summary>{row_html(record, level)}</summary><div class="gantt-children">{children_html}</div></details>'

    for root_record in sort_records(roots):
        body_rows.append(render_tree(root_record, 0))

    scale_header_html = "".join(
        f'<div class="gantt-scale-cell {"weekend" if scale == "day" and units[index].weekday() >= 5 else ""}">{escape(label)}</div>'
        for index, label in enumerate(headers)
    )

    gantt_html = f"""
    <style>
    .gantt-shell {{ border:1px solid #e5e7eb; border-radius:16px; background:#fff; overflow:auto; max-height:760px; width:100%; }}
    .gantt-header, .gantt-row {{ display:grid; grid-template-columns: 260px 90px 110px 110px 110px 160px {len(units) * column_width}px; align-items:center; }}
    .gantt-header {{ position:sticky; top:0; z-index:2; background:#f8fafc; border-bottom:1px solid #e5e7eb; font-weight:700; }}
    .gantt-cell {{ padding:8px 10px; border-right:1px solid #eef2f7; font-size:12px; min-height:42px; display:flex; align-items:center; }}
    .gantt-name {{ font-weight:600; }}
    .gantt-row {{ border-bottom:1px solid #f1f5f9; }}
    .gantt-row.level-1 .gantt-name {{ padding-left:22px; }}
    .gantt-row.level-2 .gantt-name {{ padding-left:40px; }}
    .gantt-timeline {{ position:relative; height:42px; background-image: linear-gradient(to right, #eef2f7 1px, transparent 1px); background-size:{column_width}px 100%; overflow:hidden; }}
    .gantt-weekend-bg {{ position:absolute; top:0; bottom:0; background:#f1f5f9; z-index:0; }}
    .gantt-span {{ position:absolute; top:16px; height:10px; border-radius:999px; background:rgba(59,130,246,0.16); border:1px solid rgba(59,130,246,0.28); z-index:1; }}
    .gantt-mark {{ position:absolute; top:8px; font-size:16px; line-height:1; z-index:2; }}
    .gantt-mark.start {{ color:#2563eb; }}
    .gantt-mark.due {{ color:#0f766e; }}
    .gantt-mark.deadline {{ color:#b91c1c; }}
    .gantt-scale {{ display:grid; grid-template-columns: repeat({len(units)}, {column_width}px); }}
    .gantt-scale-cell {{ padding:8px 0; text-align:center; font-size:11px; border-right:1px solid #eef2f7; }}
    .gantt-scale-cell.weekend {{ background:#f1f5f9; }}
    .gantt-details > summary {{ list-style:none; cursor:pointer; }}
    .gantt-details > summary::-webkit-details-marker {{ display:none; }}
    .gantt-children {{ }}
    </style>
    <div class="gantt-shell">
      <div class="gantt-header">
        <div class="gantt-cell">Elem</div>
        <div class="gantt-cell">Típus</div>
        <div class="gantt-cell">Start</div>
        <div class="gantt-cell">Due</div>
        <div class="gantt-cell">Deadline</div>
        <div class="gantt-cell">Kapcsolatok</div>
        <div class="gantt-scale">{scale_header_html}</div>
      </div>
      {"".join(body_rows)}
    </div>
    """
    components.html(gantt_html, height=820, scrolling=True)

    gantt_edit_ids = [record.record_id for record in visible_records]
    gantt_selected_id = st.selectbox(
        "GANTT szerkesztés",
        options=gantt_edit_ids,
        format_func=lambda record_id: record_label(lookup[record_id]),
        key="gantt_selected_record_id",
    )
    gantt_selected = lookup[gantt_selected_id]
    edit_col1, edit_col2, edit_col3, edit_col4 = st.columns(4)
    gantt_title = edit_col1.text_input("Név", value=gantt_selected.title, key=f"gantt_title_{gantt_selected.record_id}")
    edit_col2.caption(f"Típus: {gantt_selected.entity_type}")
    gantt_start = edit_col2.date_input("Start", value=parse_optional_date(gantt_selected.start_at), key=f"gantt_start_{gantt_selected.record_id}")
    gantt_due = edit_col3.date_input("Due", value=parse_optional_date(gantt_selected.due_at), key=f"gantt_due_{gantt_selected.record_id}")
    gantt_deadline = edit_col4.date_input("Deadline", value=parse_optional_date(gantt_selected.deadline), key=f"gantt_deadline_{gantt_selected.record_id}")
    if st.button("GANTT sor mentése", key=f"gantt_save_{gantt_selected.record_id}"):
        updated_record = update_record(
            gantt_selected,
            title=gantt_title.strip(),
            start_at=gantt_start.isoformat() if isinstance(gantt_start, date) else None,
            due_at=gantt_due.isoformat() if isinstance(gantt_due, date) else None,
            deadline=gantt_deadline.isoformat() if isinstance(gantt_deadline, date) else None,
        )
        persist_record(with_synced_hierarchy_title(updated_record), source_dir, config, records_path, index_path, chroma_dir, history_events_path)


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


def persist_record(record: KnowledgeRecord, source_dir, config, records_path, index_path, chroma_dir, history_path) -> None:
    saved = upsert_record(records_path, record, history_path=history_path, source="ui")
    st.session_state["selected_record_id"] = saved.record_id
    st.session_state["execution_selected_record_id"] = saved.record_id
    refreshed_records = load_records(records_path)
    chunks = build_index(source_dir, config)
    save_index(index_path, chunks)
    st.success(f"Mentve: {saved.record_id}")
    st.info(f"Keyword index frissítve: {len(chunks)} chunk")
    try:
        count = upsert_manual_records(refreshed_records, chroma_dir, config.ollama_embed_model)
        st.info(f"Chroma upsert kesz: {count} rekord.")
    except VectorStoreError as exc:
        st.warning(f"Chroma upsert kihagyva: {exc}")
    st.rerun()


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_next_steps(value: str) -> list[dict]:
    steps: list[dict] = []
    for raw_line in (value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        done = line.startswith("[x]") or line.startswith("[X]")
        if line.startswith("[x]") or line.startswith("[X]") or line.startswith("[ ]"):
            line = line[3:].strip()
        parts = [part.strip() for part in line.split("|")]
        title = parts[0] if parts else ""
        estimate = parts[1] if len(parts) > 1 else ""
        if title:
            steps.append({"title": title, "estimate": estimate, "done": done})
    return steps


def format_next_steps(steps: list[dict]) -> str:
    lines: list[str] = []
    for item in steps or []:
        if not isinstance(item, dict):
            continue
        prefix = "[x]" if item.get("done") else "[ ]"
        title = str(item.get("title", "")).strip()
        estimate = str(item.get("estimate", "")).strip()
        if not title:
            continue
        lines.append(f"{prefix} {title}" + (f" | {estimate}" if estimate else ""))
    return "\n".join(lines)


def markdown_import_entity(line: str) -> tuple[str, str, str]:
    text = line.strip()
    if text.startswith("[ ]"):
        return text[3:].strip(), "task", "inbox"
    if text.startswith("[x]") or text.startswith("[X]"):
        return text[3:].strip(), "task", "done"
    return text, "note", "inbox"


def parse_markdown_hierarchy(markdown_text: str) -> list[dict]:
    nodes: list[dict] = []
    stack: list[dict] = []
    last_node: dict | None = None

    for raw_line in (markdown_text or "").splitlines():
        if not raw_line.strip():
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", raw_line)
        list_match = re.match(r"^(\s*)[-*+]\s+(.*)$", raw_line)

        if heading_match:
            depth = max(len(heading_match.group(1)) - 1, 0)
            title, entity_type, status = markdown_import_entity(heading_match.group(2))
        elif list_match:
            indent = len(list_match.group(1).replace("\t", "  "))
            depth = indent // 2
            title, entity_type, status = markdown_import_entity(list_match.group(2))
        else:
            if last_node is not None:
                extra = raw_line.strip()
                if extra:
                    last_node["content_lines"].append(extra)
            continue

        title = title.strip()
        if not title:
            continue

        while len(stack) > depth:
            stack.pop()
        parent_temp_id = stack[-1]["temp_id"] if stack else ""
        temp_id = f"import-{len(nodes)}"
        node = {
            "temp_id": temp_id,
            "parent_temp_id": parent_temp_id,
            "title": title,
            "entity_type": entity_type,
            "status": status,
            "content_lines": [],
        }
        nodes.append(node)
        stack.append(node)
        last_node = node

    return nodes


def build_records_from_markdown_import(parent_record: KnowledgeRecord, markdown_text: str) -> list[KnowledgeRecord]:
    parsed_nodes = parse_markdown_hierarchy(markdown_text)
    if not parsed_nodes:
        return []

    created: list[KnowledgeRecord] = []
    temp_to_record_id: dict[str, str] = {}

    for item in parsed_nodes:
        parent_id = temp_to_record_id.get(item["parent_temp_id"], parent_record.record_id)
        content = "\n\n".join(line for line in item.get("content_lines", []) if line.strip())
        created_record = KnowledgeRecord(
            record_id=build_record_id(item["title"]),
            title=item["title"],
            summary="",
            content=content,
            source_type="manual",
            entity_type=item["entity_type"],
            status=item["status"],
            organization=parent_record.organization,
            team=parent_record.team,
            project=parent_record.project,
            case_name=parent_record.case_name,
            parent_id=parent_id,
        )
        created.append(created_record)
        temp_to_record_id[item["temp_id"]] = created_record.record_id

    return created


def normalize_table_value(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def persist_records_bulk(records: list[KnowledgeRecord], source_dir, config, records_path, index_path, chroma_dir, history_path, success_message: str) -> None:
    replace_records(records_path, records, history_path=history_path, source="ui")
    refreshed_records = load_records(records_path)
    chunks = build_index(source_dir, config)
    save_index(index_path, chunks)
    st.success(success_message)
    st.info(f"Keyword index frissítve: {len(chunks)} chunk")
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
    updated = update_record(
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
        graph_edges=list(existing.graph_edges),
        start_at=start_at,
        due_at=due_at,
        deadline=deadline,
        event_at=event_at,
        next_step=normalize_table_value(row.get("next_step")) or existing.next_step,
        next_step_estimate=normalize_table_value(row.get("next_step_estimate")) or existing.next_step_estimate,
        next_steps=list(existing.next_steps),
        planning_bucket=normalize_table_value(row.get("planning_bucket")),
        focus_rank=focus_rank,
    )
    return with_synced_hierarchy_title(updated)


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


def infer_parent_from_hierarchy(
    records: list[KnowledgeRecord],
    entity_type: str,
    hierarchy_values: dict[str, str],
    current_record_id: str = "",
) -> str:
    if entity_type == "organization":
        return ""

    organization = hierarchy_values.get("organization", "").strip()
    team = hierarchy_values.get("team", "").strip()
    project = hierarchy_values.get("project", "").strip()
    case_name = hierarchy_values.get("case_name", "").strip()

    def match(predicate) -> str:
        for record in records:
            if current_record_id and record.record_id == current_record_id:
                continue
            if predicate(record):
                return record.record_id
        return ""

    if entity_type == "team":
        return match(lambda record: record.entity_type == "organization" and (record.organization or record.title) == organization)
    if entity_type == "project":
        return (
            match(lambda record: record.entity_type == "team" and record.organization == organization and (record.team or record.title) == team)
            or match(lambda record: record.entity_type == "organization" and (record.organization or record.title) == organization)
        )
    if entity_type == "case":
        return (
            match(
                lambda record: record.entity_type == "project"
                and record.organization == organization
                and record.team == team
                and (record.project or record.title) == project
            )
            or match(lambda record: record.entity_type == "team" and record.organization == organization and (record.team or record.title) == team)
            or match(lambda record: record.entity_type == "organization" and (record.organization or record.title) == organization)
        )
    return (
        match(
            lambda record: record.entity_type == "case"
            and record.organization == organization
            and record.team == team
            and record.project == project
            and (record.case_name or record.title) == case_name
        )
        or match(
            lambda record: record.entity_type == "project"
            and record.organization == organization
            and record.team == team
            and (record.project or record.title) == project
        )
        or match(lambda record: record.entity_type == "team" and record.organization == organization and (record.team or record.title) == team)
        or match(lambda record: record.entity_type == "organization" and (record.organization or record.title) == organization)
    )


def is_descendant(records: list[KnowledgeRecord], ancestor_id: str, candidate_id: str) -> bool:
    children_by_parent: dict[str, list[str]] = {}
    for record in records:
        if record.parent_id:
            children_by_parent.setdefault(record.parent_id, []).append(record.record_id)
    queue = list(children_by_parent.get(ancestor_id, []))
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == candidate_id:
            return True
        queue.extend(children_by_parent.get(current, []))
    return False


def reparent_subtree(records: list[KnowledgeRecord], moved_id: str, new_parent_id: str) -> list[KnowledgeRecord]:
    lookup = {record.record_id: record for record in records}
    moved_record = lookup.get(moved_id)
    new_parent = lookup.get(new_parent_id)
    if not moved_record or not new_parent:
        return records

    children_by_parent: dict[str, list[str]] = {}
    for record in records:
        if record.parent_id:
            children_by_parent.setdefault(record.parent_id, []).append(record.record_id)

    def apply_under_parent(record: KnowledgeRecord, parent_record: KnowledgeRecord | None) -> KnowledgeRecord:
        parent_hierarchy = hierarchy_from_record(parent_record) if parent_record else {"organization": "", "team": "", "project": "", "case_name": ""}
        changes: dict[str, str] = {}
        if record.entity_type == "organization":
            changes["organization"] = record.title
            changes["team"] = ""
            changes["project"] = ""
            changes["case_name"] = ""
        elif record.entity_type == "team":
            changes["organization"] = parent_hierarchy["organization"]
            changes["team"] = record.title
            changes["project"] = ""
            changes["case_name"] = ""
        elif record.entity_type == "project":
            changes["organization"] = parent_hierarchy["organization"]
            changes["team"] = parent_hierarchy["team"]
            changes["project"] = record.title
            changes["case_name"] = ""
        elif record.entity_type == "case":
            changes["organization"] = parent_hierarchy["organization"]
            changes["team"] = parent_hierarchy["team"]
            changes["project"] = parent_hierarchy["project"]
            changes["case_name"] = record.title
        else:
            changes["organization"] = parent_hierarchy["organization"]
            changes["team"] = parent_hierarchy["team"]
            changes["project"] = parent_hierarchy["project"]
            changes["case_name"] = parent_hierarchy["case_name"]
        return with_synced_hierarchy_title(update_record(record, **changes, updated_at=utc_now_iso()))

    updated_lookup = {record.record_id: record for record in records}
    updated_lookup[moved_id] = apply_under_parent(update_record(moved_record, parent_id=new_parent_id, updated_at=utc_now_iso()), new_parent)

    queue = list(children_by_parent.get(moved_id, []))
    while queue:
        child_id = queue.pop(0)
        child_record = updated_lookup.get(child_id)
        if not child_record:
            continue
        parent_record = updated_lookup.get(child_record.parent_id)
        updated_lookup[child_id] = apply_under_parent(child_record, parent_record)
        queue.extend(children_by_parent.get(child_id, []))

    merged_records = [updated_lookup.get(record.record_id, record) for record in records]
    return sync_hierarchy_renames(merged_records, lookup)


def remove_record_and_reparent_children(records: list[KnowledgeRecord], record_id: str) -> list[KnowledgeRecord]:
    lookup = {record.record_id: record for record in records}
    removed = lookup.get(record_id)
    if not removed:
        return records

    updated_records: list[KnowledgeRecord] = []
    for record in records:
        if record.record_id == record_id:
            continue
        if record.parent_id == record_id:
            updated_records.append(update_record(record, parent_id=removed.parent_id, updated_at=utc_now_iso()))
        else:
            updated_records.append(record)
    return sync_hierarchy_renames(updated_records, lookup)


def hierarchy_fields_for(entity_type: str) -> list[str]:
    mapping = {
        "area": [],
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
    allow_parent_edit: bool = True,
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

    show_status = entity_type not in {"area", "organization", "team", "person"}
    show_people = entity_type in {"organization", "team", "project", "case", "task", "event", "note", "source_item", "decision"}
    show_parent = allow_parent_edit
    show_schedule = entity_type in {"task", "case", "project", "decision", "note", "source_item"}
    show_event = entity_type == "event"
    show_decision = entity_type in {"project", "case", "task", "decision", "note", "source_item"}
    show_planning = entity_type in {"task", "case", "project", "decision", "note", "source_item", "event"}
    show_next_steps = entity_type == "task"

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

    if not allow_parent_edit:
        if entity_type in {"area", "organization"}:
            parent_id = record.parent_id
        elif entity_type in {"team", "project", "case"}:
            parent_id = infer_parent_from_hierarchy(records, entity_type, hierarchy_values, record.record_id)
        else:
            parent_id = record.parent_id or infer_parent_from_hierarchy(records, entity_type, hierarchy_values, record.record_id)

    summary = st.text_area("Rovid osszefoglalo", value=record.summary, height=100, key=f"{key_prefix}_summary")
    content = st.text_area(
        "Reszletes tartalom / Markdown jegyzet",
        value=record.content,
        height=320,
        help="A sima beillesztett markdown itt nyers markdownkent mentodik el. Később egy gazdagabb editor is ugyanebből tud dolgozni.",
        key=f"{key_prefix}_content",
    )

    tag_col, relation_col = st.columns(2)
    tags_raw = tag_col.text_input(
        "Tagek",
        value=", ".join(record.tags),
        help="Vesszovel elvalasztva",
        key=f"{key_prefix}_tags",
    )
    with relation_col:
        relations = render_relations_selector(records, record.relations, key_prefix)
    graph_edges = reconcile_graph_edges_with_relations(record.graph_edges, relations, record.record_id)

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

    if show_next_steps:
        step_col1, step_col2 = st.columns(2)
        next_step = step_col1.text_input("Next step", value=record.next_step, key=f"{key_prefix}_next_step")
        next_step_estimate = step_col2.text_input("Becsult ido", value=record.next_step_estimate, key=f"{key_prefix}_next_step_estimate", placeholder="pl. 25p vagy 1h")
        next_steps_raw = st.text_area(
            "Next steps lista",
            value=format_next_steps(record.next_steps),
            height=120,
            help="Soronkent: [ ] Lepes | 25p vagy [x] Lepes | 1h",
            key=f"{key_prefix}_next_steps",
        )
    else:
        next_step = ""
        next_step_estimate = ""
        next_steps_raw = ""

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
        "graph_edges": graph_edges,
        "decision_needed": decision_needed,
        "decision_context": decision_context.strip(),
        "start_at": start_at.isoformat() if isinstance(start_at, date) else None,
        "due_at": due_at.isoformat() if isinstance(due_at, date) else None,
        "deadline": deadline.isoformat() if isinstance(deadline, date) else None,
        "event_at": event_at.isoformat() if isinstance(event_at, date) else None,
        "next_step": next_step.strip(),
        "next_step_estimate": next_step_estimate.strip(),
        "next_steps": parse_next_steps(next_steps_raw),
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
            add_node(team_node, record.team, "hexagon", "#67E8F9", "filled")
            if org_node:
                add_edge(org_node, team_node, "contains")
        if record.project:
            project_node = f"project::{record.organization}::{record.team}::{record.project}"
            add_node(project_node, record.project, "box", "#86EFAC", "rounded,filled")
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


def filter_context_graph_records(
    records: list[KnowledgeRecord],
    entity_filters: list[str],
    status_filters: list[str],
    project_filter: str,
    case_filter: str,
    active_only: bool,
    due_from: date | None,
    due_to: date | None,
) -> list[KnowledgeRecord]:
    filtered = records
    if entity_filters:
        filtered = [record for record in filtered if record.entity_type in entity_filters]
    if status_filters:
        filtered = [record for record in filtered if record.status in status_filters]
    if project_filter.strip():
        needle = project_filter.strip().lower()
        filtered = [record for record in filtered if needle in record.project.lower()]
    if case_filter.strip():
        needle = case_filter.strip().lower()
        filtered = [record for record in filtered if needle in record.case_name.lower()]
    if active_only:
        filtered = [record for record in filtered if record.status in {"inbox", "next", "active", "waiting"}]
    if due_from:
        due_from_iso = due_from.isoformat()
        filtered = [record for record in filtered if record.due_at and record.due_at >= due_from_iso]
    if due_to:
        due_to_iso = due_to.isoformat()
        filtered = [record for record in filtered if record.due_at and record.due_at <= due_to_iso]
    return filtered


def expand_context_graph_with_ancestors(records: list[KnowledgeRecord], base_records: list[KnowledgeRecord]) -> list[KnowledgeRecord]:
    lookup = {record.record_id: record for record in records}
    selected: dict[str, KnowledgeRecord] = {record.record_id: record for record in base_records}
    queue = [record.record_id for record in base_records]
    while queue:
        record_id = queue.pop(0)
        record = lookup.get(record_id)
        if not record or not record.parent_id:
            continue
        parent = lookup.get(record.parent_id)
        if parent and parent.record_id not in selected:
            selected[parent.record_id] = parent
            queue.append(parent.record_id)
    return list(selected.values())


def build_context_graph_payload(records: list[KnowledgeRecord], selected_node_id: str, show_relations: bool, show_only_hierarchy: bool, mode: str, force_fit_token: str = "") -> dict:
    filtered_lookup = {record.record_id: record for record in records}
    hierarchy_lookup: dict[str, str] = {}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys: set[tuple[str, str, str]] = set()

    def synthetic_id(kind: str, *parts: str) -> str:
        return f"{kind}::" + "::".join(part for part in parts if part)

    def add_node(node_id: str, label: str, entity_type: str, status: str = "", project: str = "", case_name: str = "", due_at: str | None = None, synthetic: bool = False) -> None:
        if node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "entity_type": entity_type,
            "status": status,
            "project": project,
            "case_name": case_name,
            "due_at": due_at or "",
            "synthetic": synthetic,
        }

    def add_edge(source: str, target: str, kind: str, relation_type: str = "", label: str = "") -> None:
        if not source or not target or source == target:
            return
        edge_key = (source, target, kind, relation_type, label)
        if edge_key in edge_keys:
            return
        edge_keys.add(edge_key)
        edges.append(
            {
                "id": f"{kind}:{relation_type}:{source}:{target}:{label}",
                "source": source,
                "target": target,
                "kind": kind,
                "relation_type": relation_type,
                "label": label,
            }
        )

    for record in records:
        add_node(
            record.record_id,
            record.title,
            record.entity_type,
            status=record.status,
            project=record.project,
            case_name=record.case_name,
            due_at=record.due_at,
            synthetic=False,
        )
        if record.entity_type == "organization" and record.organization:
            hierarchy_lookup[synthetic_id("organization", record.organization)] = record.record_id
        if record.entity_type == "team" and record.organization and record.team:
            hierarchy_lookup[synthetic_id("team", record.organization, record.team)] = record.record_id
        if record.entity_type == "project" and record.organization and record.team and record.project:
            hierarchy_lookup[synthetic_id("project", record.organization, record.team, record.project)] = record.record_id
        if record.entity_type == "case" and record.organization and record.team and record.project and record.case_name:
            hierarchy_lookup[synthetic_id("case", record.organization, record.team, record.project, record.case_name)] = record.record_id

    def ensure_hierarchy_node(kind: str, label: str, *parts: str) -> str:
        synth_id = synthetic_id(kind, *parts)
        existing = hierarchy_lookup.get(synth_id)
        if existing:
            return existing
        add_node(synth_id, label, kind, synthetic=True)
        hierarchy_lookup[synth_id] = synth_id
        return synth_id

    for record in records:
        org_node = None
        team_node = None
        project_node = None
        case_node = None

        if record.organization:
            org_node = ensure_hierarchy_node("organization", record.organization, record.organization)
        if record.organization and record.team:
            team_node = ensure_hierarchy_node("team", record.team, record.organization, record.team)
            if org_node:
                add_edge(org_node, team_node, "hierarchy")
        if record.organization and record.team and record.project:
            project_node = ensure_hierarchy_node("project", record.project, record.organization, record.team, record.project)
            if team_node:
                add_edge(team_node, project_node, "hierarchy")
            elif org_node:
                add_edge(org_node, project_node, "hierarchy")
        if record.organization and record.team and record.project and record.case_name:
            case_node = ensure_hierarchy_node("case", record.case_name, record.organization, record.team, record.project, record.case_name)
            if project_node:
                add_edge(project_node, case_node, "hierarchy")

        if record.parent_id and record.parent_id in filtered_lookup:
            add_edge(record.parent_id, record.record_id, "hierarchy")
        elif record.entity_type not in {"area", "organization", "team", "project", "case"}:
            parent_node = case_node or project_node or team_node or org_node
            if parent_node:
                add_edge(parent_node, record.record_id, "hierarchy")

        if show_relations and not show_only_hierarchy:
            for edge in normalize_graph_edges(record.graph_edges, record.relations, record.record_id):
                relation_id = edge.get("target_id", "")
                if relation_id in filtered_lookup:
                    add_edge(
                        record.record_id,
                        relation_id,
                        "relation",
                        relation_type=edge.get("relation_type", "related_to"),
                        label=edge.get("label", ""),
                    )

    return {
        "mode": mode,
        "selected_node_id": selected_node_id,
        "force_fit_token": force_fit_token,
        "nodes": list(nodes.values()),
        "edges": [edge for edge in edges if not show_only_hierarchy or edge["kind"] == "hierarchy"],
    }


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

        remove_col1, remove_col2 = st.columns(2)
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
        st.caption("Kezzel felvett blokk torlese: a jobb oldali execution nezet napjanal a blokk melletti `x` gombbal.")


def render_execution_graph(records: list[KnowledgeRecord], layout: dict, layout_path, source_dir, config, records_path, index_path, chroma_dir, history_path) -> None:
    st.subheader("Execution Graph")
    st.caption("Vizuális, drag-and-drop planning felület hét -> nap -> blokk szerkezettel. A blokkhoz tartozó nap dátuma a task `due` mezőjével kerül összhangba, a `deadline` ettől független marad.")
    render_execution_layout_manager(layout, layout_path)
    pending_bucket = st.session_state.pop("pending_execution_filter_bucket", None)
    if pending_bucket is not None:
        st.session_state["execution_filter_bucket"] = pending_bucket

    with st.expander("Szűrés", expanded=False):
        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        exec_project = filter_col1.text_input("Projekt", key="execution_filter_project")
        exec_org = filter_col2.text_input("Munkahely / organization", key="execution_filter_org")
        exec_status = filter_col3.selectbox("Státusz", [NONE_OPTION] + STATUS_OPTIONS, key="execution_filter_status")
        exec_bucket = filter_col4.selectbox(
            "Tervezési hely",
            [NONE_OPTION] + planning_bucket_options(layout),
            format_func=lambda value: NONE_OPTION if value == NONE_OPTION else planning_bucket_label(value, planning_bucket_titles(layout)),
            key="execution_filter_bucket",
        )
        filter_col5, filter_col6, filter_col7 = st.columns(3)
        exec_due_from = filter_col5.date_input("Due - tól", value=st.session_state.get("execution_filter_due_from"), key="execution_filter_due_from")
        exec_due_to = filter_col6.date_input("Due - ig", value=st.session_state.get("execution_filter_due_to"), key="execution_filter_due_to")
        exec_text = filter_col7.text_input("Szöveges szűrés", key="execution_filter_text")
        exec_only_with_content = st.checkbox("Csak ahol van tartalom", value=st.session_state.get("execution_only_with_content", False), key="execution_only_with_content")

    task_records = [record for record in records if record.entity_type == "task"]
    filtered_task_records = task_records
    if exec_project.strip():
        needle = exec_project.strip().lower()
        filtered_task_records = [record for record in filtered_task_records if needle in record.project.lower()]
    if exec_org.strip():
        needle = exec_org.strip().lower()
        filtered_task_records = [record for record in filtered_task_records if needle in record.organization.lower()]
    if exec_status != NONE_OPTION:
        filtered_task_records = [record for record in filtered_task_records if record.status == exec_status]
    if exec_bucket != NONE_OPTION:
        filtered_task_records = [record for record in filtered_task_records if record.planning_bucket == exec_bucket]
    if exec_due_from:
        due_from_iso = exec_due_from.isoformat()
        filtered_task_records = [record for record in filtered_task_records if record.due_at and record.due_at >= due_from_iso]
    if exec_due_to:
        due_to_iso = exec_due_to.isoformat()
        filtered_task_records = [record for record in filtered_task_records if record.due_at and record.due_at <= due_to_iso]
    if exec_text.strip():
        needle = exec_text.strip().lower()
        filtered_task_records = [
            record
            for record in filtered_task_records
            if needle in record.title.lower()
            or needle in record.summary.lower()
            or needle in record.project.lower()
            or needle in record.case_name.lower()
            or needle in record.next_step.lower()
        ]

    if not filtered_task_records:
        st.info("Meg nincs task rekord az execution graphhoz.")
        return

    component_payload = {
        "sections": filter_execution_sections_by_content(build_execution_sections(layout), filtered_task_records, exec_only_with_content),
        "tasks": [
            {
                "record_id": record.record_id,
                "title": record.title,
                "project": record.project,
                "case_name": record.case_name,
                "planning_bucket": record.planning_bucket,
                "due_at": record.due_at,
                "focus_rank": record.focus_rank,
            }
            for record in filtered_task_records
        ],
    }

    drag_result = execution_dnd_board(component_payload, key="execution_dnd_surface")
    if isinstance(drag_result, dict) and drag_result.get("action") in {"move_task", "add_block", "remove_block", "rename_day", "rename_week"}:
        event_id = str(drag_result.get("event_id", "")).strip()
        if event_id and st.session_state.get("last_execution_drag_event") == event_id:
            drag_result = None
        elif event_id:
            st.session_state["last_execution_drag_event"] = event_id
    if isinstance(drag_result, dict) and drag_result.get("action") == "move_task":
        record_id = str(drag_result.get("record_id", "")).strip()
        planning_bucket = str(drag_result.get("planning_bucket", "")).strip()
        dragged_record = next((record for record in task_records if record.record_id == record_id), None)
        normalized_bucket = "" if planning_bucket == "__unscheduled__" else planning_bucket
        if dragged_record and normalized_bucket != dragged_record.planning_bucket:
            bucket_info = day_for_bucket(layout, normalized_bucket) if normalized_bucket else None
            next_due = date.today().isoformat() if normalized_bucket == "main_focus" else (bucket_info.get("day_date") if bucket_info else None)
            persist_record(
                update_record(
                    dragged_record,
                    planning_bucket=normalized_bucket,
                    due_at=next_due,
                    focus_rank=None,
                ),
                source_dir,
                config,
                records_path,
                index_path,
                chroma_dir,
                history_path,
            )
    if isinstance(drag_result, dict) and drag_result.get("action") == "add_block":
        day_key = str(drag_result.get("day_key", "")).strip()
        title = str(drag_result.get("title", "")).strip()
        if day_key and title:
            persist_planning_layout(add_block(layout, day_key, title, "session"), layout_path)
    if isinstance(drag_result, dict) and drag_result.get("action") == "rename_day":
        day_key = str(drag_result.get("day_key", "")).strip()
        title = str(drag_result.get("title", "")).strip()
        if day_key and title and find_day(layout, day_key):
            persist_planning_layout(rename_day(layout, day_key, title), layout_path)
    if isinstance(drag_result, dict) and drag_result.get("action") == "rename_week":
        week_key = str(drag_result.get("week_key", "")).strip()
        title = str(drag_result.get("title", "")).strip()
        if week_key and title:
            persist_planning_layout(rename_week(layout, week_key, title), layout_path)
    if isinstance(drag_result, dict) and drag_result.get("action") == "remove_block":
        block_key = str(drag_result.get("block_key", "")).strip()
        block_info = find_block(layout, block_key) if block_key else None
        if block_info and block_info.get("lane") == "session":
            must_bucket = must_bucket_for_day(layout, block_info.get("day_key", ""))
            updated_records = records
            if must_bucket:
                updated_records = [
                    update_record(record, planning_bucket=must_bucket, due_at=block_info.get("day_date"))
                    if record.planning_bucket == block_key
                    else record
                    for record in records
                ]
            persist_layout_and_records(
                remove_block(layout, block_key),
                layout_path,
                updated_records,
                source_dir,
                config,
                records_path,
                index_path,
                chroma_dir,
                history_path,
                "Blokk törölve, a taskok aznapi Mindenképp blokkba kerültek.",
            )

    known_bucket_keys = {bucket.get("key", "") for bucket in iter_buckets(layout)}
    orphan_tasks = [record for record in task_records if record.planning_bucket and record.planning_bucket not in known_bucket_keys]
    if orphan_tasks:
        st.warning("Van olyan task, ami már nem létező blokkba mutat. Ezeket érdemes újra elhelyezni.")
        for record in orphan_tasks:
            st.write(f"{record.title} -> {record.planning_bucket}")

    st.divider()
    st.markdown("**Task history**")
    valid_task_ids = [record.record_id for record in filtered_task_records]
    selected_execution_id = st.session_state.get("execution_selected_record_id")
    if selected_execution_id not in valid_task_ids and valid_task_ids:
        selected_execution_id = valid_task_ids[0]
        st.session_state["execution_selected_record_id"] = selected_execution_id

    if valid_task_ids:
        selected_execution_id = st.selectbox(
            "Task kiválasztása",
            options=valid_task_ids,
            index=valid_task_ids.index(selected_execution_id) if selected_execution_id in valid_task_ids else 0,
            format_func=lambda record_id: record_label(next(record for record in filtered_task_records if record.record_id == record_id)),
            key="execution_history_record_picker",
        )
        st.session_state["execution_selected_record_id"] = selected_execution_id
        selected_task = next((record for record in filtered_task_records if record.record_id == selected_execution_id), None)
        if selected_task:
            action_col1, action_col2 = st.columns([1, 1])
            if action_col1.button("Részlet", key=f"execution_open_detail_{selected_task.record_id}"):
                st.session_state["selected_record_id"] = selected_task.record_id
                st.rerun()
            history_toggle_key = f"execution_show_history_{selected_task.record_id}"
            if action_col2.button("History", key=f"execution_history_toggle_{selected_task.record_id}"):
                st.session_state[history_toggle_key] = not st.session_state.get(history_toggle_key, False)
            if st.session_state.get(history_toggle_key, False):
                render_record_history(history_path, selected_task.record_id, key_prefix=f"execution_{selected_task.record_id}")


def render_record_history(history_path, record_id: str, key_prefix: str = "history") -> None:
    events = [event for event in reversed(load_events(history_path)) if event.get("record_id") == record_id]
    if not events:
        st.info("Ehhez a rekordhoz még nincs naplózott előzmény.")
        return
    for event in events[:30]:
        changed = ", ".join(event.get("changed_fields", [])) or "-"
        st.markdown(f"**{event.get('action_type', '-') }**  `{event.get('timestamp', '-')}`")
        st.caption(f"forrás: {event.get('source', '-')} | mezők: {changed}")
        if event.get("movement"):
            movement = event["movement"]
            st.caption(f"áthelyezés: {movement.get('from', '') or 'nincs'} -> {movement.get('to', '') or 'nincs'}")
        before = event.get("before") or {}
        after = event.get("after") or {}
        with st.expander("Részletek", expanded=False):
            st.write({"előtte": before, "utána": after})


def inject_app_shell_css() -> None:
    st.markdown(
        """
        <style>
        .rag-topline {
          background: rgba(255,255,255,0.96);
          padding: 6px 0 10px 0; margin-bottom: 2px;
          font-size: 0.98rem; font-weight: 600; color: #334155; white-space: nowrap; overflow-x: auto;
        }
        div[data-testid="stTabs"] [data-baseweb="tab-list"] {
          display: flex !important;
        }
        button[role="tab"] {
          font-size: 0.95rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_app_topline() -> None:
    st.markdown('<div class="rag-topline">RAG asszisztens - domain-modellre épülő személyes tudás- és ügykezelő rendszer a privát RAG-DB fölött.</div>', unsafe_allow_html=True)


def inject_tab_bar_behavior() -> None:
    target_tab = st.session_state.pop("pending_main_tab", "")
    components.html(
        f"""
        <script>
        const target = {target_tab!r};
        const doc = window.parent.document;
        function syncStickyHeader() {{
          const topLine = doc.querySelector(".rag-topline");
          const tabsRoot = doc.querySelector('div[data-testid="stTabs"]');
          const tabList = tabsRoot ? (tabsRoot.querySelector('[data-baseweb="tab-list"]') || tabsRoot.querySelector('div[role="tablist"]')) : null;
          const tabButtons = Array.from(doc.querySelectorAll('button[role="tab"]'));
          if (!topLine || !tabsRoot || !tabList || !tabButtons.length) return false;

          topLine.style.position = "fixed";
          topLine.style.top = "0";
          topLine.style.left = "0";
          topLine.style.right = "0";
          topLine.style.zIndex = "1002";
          topLine.style.margin = "0";
          topLine.style.padding = "6px 14px 8px 14px";
          topLine.style.background = "rgba(255,255,255,0.98)";
          topLine.style.borderBottom = "1px solid #e5e7eb";

          tabList.style.position = "fixed";
          tabList.style.top = `${{topLine.offsetHeight}}px`;
          tabList.style.left = "0";
          tabList.style.right = "0";
          tabList.style.zIndex = "1001";
          tabList.style.margin = "0";
          tabList.style.padding = "6px 14px 8px 14px";
          tabList.style.background = "rgba(255,255,255,0.98)";
          tabList.style.borderBottom = "1px solid #e5e7eb";

          const panelWrap = tabsRoot.querySelector(':scope > div:nth-child(2)');
          if (panelWrap) {{
            panelWrap.style.marginTop = `${{topLine.offsetHeight + tabList.offsetHeight + 8}}px`;
          }}

          if (target) {{
            const match = tabButtons.find(button => (button.innerText || "").trim() === target);
            if (match) match.click();
          }}
          return true;
        }};
        let attempts = 0;
        const timer = setInterval(() => {{
          attempts += 1;
          if (syncStickyHeader() || attempts > 50) {{
            clearInterval(timer);
          }}
        }}, 120);
        </script>
        """,
        height=0,
    )


def app() -> None:
    config = load_config()
    st.set_page_config(page_title="RAG asszisztens", layout="wide")
    inject_app_shell_css()
    render_app_topline()

    if not config.source_dir:
        st.error("A .env fajlban hianyzik a RAG_SOURCE_DIR beallitas.")
        return

    source_dir = config.source_dir.resolve()
    records_path = config.manual_records_path_for(source_dir)
    index_path = config.index_path_for(source_dir)
    chroma_dir = config.chroma_dir_for(source_dir)
    planning_layout_path = config.planning_layout_path_for(source_dir)
    history_events_path = config.history_events_path_for(source_dir)
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
    st.sidebar.write(f"History log: `{history_events_path}`")
    if selected_record:
        st.sidebar.subheader("Kivalasztott rekord")
        st.sidebar.write(record_label(selected_record))
        st.sidebar.caption(selected_record.record_id)

    tab_execution, tab_mindmap, tab_kanban, tab_detail, tab_input, tab_table, tab_timeline, tab_search = st.tabs(
        ["Execution Graph", "Context Graph", "Kanban", "Részlet", "Bevitel", "Táblázat", "GANTT", "Keresés"]
    )
    inject_tab_bar_behavior()

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
                    relations=graph_edges_to_relations(create_values["graph_edges"]),
                    graph_edges=create_values["graph_edges"],
                    decision_needed=create_values["decision_needed"],
                    decision_context=create_values["decision_context"],
                    start_at=create_values["start_at"],
                    due_at=create_values["due_at"],
                    deadline=create_values["deadline"],
                    event_at=create_values["event_at"],
                    next_step=create_values["next_step"],
                    next_step_estimate=create_values["next_step_estimate"],
                    next_steps=create_values["next_steps"],
                    planning_bucket=create_values["planning_bucket"],
                    focus_rank=create_values["focus_rank"],
                )
                persist_record(record, source_dir, config, records_path, index_path, chroma_dir, history_events_path)

    with tab_detail:
        st.subheader("Rekord részlet és szerkesztés")
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
            st.write(f"Létrehozva: {selected_record.created_at}")
            st.write(f"Utoljára frissítve: {selected_record.updated_at}")
            history_toggle_key = f"show_history_{selected_record.record_id}"
            if st.button("History", key=f"detail_history_toggle_{selected_record.record_id}"):
                st.session_state[history_toggle_key] = not st.session_state.get(history_toggle_key, False)
            if st.session_state.get(history_toggle_key, False):
                render_record_history(history_events_path, selected_record.record_id, key_prefix=f"detail_{selected_record.record_id}")

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
                    relations=graph_edges_to_relations(edit_values["graph_edges"]),
                    graph_edges=edit_values["graph_edges"],
                    decision_needed=edit_values["decision_needed"],
                    decision_context=edit_values["decision_context"],
                    start_at=edit_values["start_at"],
                    due_at=edit_values["due_at"],
                    deadline=edit_values["deadline"],
                    event_at=edit_values["event_at"],
                    next_step=edit_values["next_step"],
                    next_step_estimate=edit_values["next_step_estimate"],
                    next_steps=edit_values["next_steps"],
                    planning_bucket=edit_values["planning_bucket"],
                    focus_rank=edit_values["focus_rank"],
                )
                persist_record(with_synced_hierarchy_title(updated_record), source_dir, config, records_path, index_path, chroma_dir, history_events_path)

    with tab_table:
        st.subheader("Tablazat nezet")
        if records:
            filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
            filter_title = filter_col1.text_input("Cim szures", key="table_filter_title")
            filter_entity = filter_col2.selectbox(
                "Szures entitasra",
                [NONE_OPTION] + ENTITY_OPTIONS,
                key="table_filter_entity",
            )
            filter_status = filter_col3.selectbox(
                "Szures statuszra",
                [NONE_OPTION] + STATUS_OPTIONS,
                key="table_filter_status",
            )
            filter_organization = filter_col4.text_input("Organization szures", key="table_filter_organization")
            filter_team = filter_col4.text_input("Team szures", key="table_filter_team")

            filter_col5, filter_col6, filter_col7, filter_col8 = st.columns(4)
            filter_project = filter_col5.text_input("Projekt szures", key="table_filter_project")
            filter_case = filter_col6.text_input("Ugy szures", key="table_filter_case")
            filter_planning_bucket = filter_col7.selectbox(
                "Tervezesi hely szures",
                [NONE_OPTION] + planning_options,
                format_func=lambda value: NONE_OPTION if value == NONE_OPTION else planning_bucket_label(value, planning_titles),
                key="table_filter_planning_bucket",
            )
            filter_text = filter_col8.text_input("Altalanos szoveges szures", key="table_filter_text")

            due_col1, due_col2 = st.columns(2)
            filter_due_from = due_col1.date_input(
                "Due date - tol",
                value=st.session_state.get("table_filter_due_from"),
                key="table_filter_due_from",
            )
            filter_due_to = due_col2.date_input(
                "Due date - ig",
                value=st.session_state.get("table_filter_due_to"),
                key="table_filter_due_to",
            )

            sort_col1, sort_col2 = st.columns(2)
            table_sort_by = sort_col1.selectbox(
                "Rendezes",
                options=["updated_at", "created_at", "title", "due_at", "deadline"],
                format_func=lambda value: {
                    "updated_at": "Utoljara frissitve",
                    "created_at": "Letrehozva",
                    "title": "Cim",
                    "due_at": "Due date",
                    "deadline": "Deadline",
                }.get(value, value),
                key="table_sort_by",
            )
            table_sort_desc = sort_col2.checkbox("Csokkeno sorrend", value=True, key="table_sort_desc")

            filtered_records = records
            if filter_title.strip():
                needle = filter_title.strip().lower()
                filtered_records = [record for record in filtered_records if needle in record.title.lower()]
            if filter_entity != NONE_OPTION:
                filtered_records = [record for record in filtered_records if record.entity_type == filter_entity]
            if filter_status != NONE_OPTION:
                filtered_records = [record for record in filtered_records if record.status == filter_status]
            if filter_organization.strip():
                needle = filter_organization.strip().lower()
                filtered_records = [record for record in filtered_records if needle in record.organization.lower()]
            if filter_team.strip():
                needle = filter_team.strip().lower()
                filtered_records = [record for record in filtered_records if needle in record.team.lower()]
            if filter_project.strip():
                needle = filter_project.strip().lower()
                filtered_records = [record for record in filtered_records if needle in record.project.lower()]
            if filter_case.strip():
                needle = filter_case.strip().lower()
                filtered_records = [record for record in filtered_records if needle in record.case_name.lower()]
            if filter_planning_bucket != NONE_OPTION:
                filtered_records = [record for record in filtered_records if record.planning_bucket == filter_planning_bucket]
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
                    or needle in record.next_step.lower()
                    or needle in record.next_step_estimate.lower()
                ]
            if filter_due_from:
                due_from_iso = filter_due_from.isoformat()
                filtered_records = [record for record in filtered_records if record.due_at and record.due_at >= due_from_iso]
            if filter_due_to:
                due_to_iso = filter_due_to.isoformat()
                filtered_records = [record for record in filtered_records if record.due_at and record.due_at <= due_to_iso]

            filtered_records.sort(
                key=lambda record: {
                    "updated_at": record.updated_at or "",
                    "created_at": record.created_at or "",
                    "title": record.title.lower(),
                    "due_at": record.due_at or "",
                    "deadline": record.deadline or "",
                }.get(table_sort_by, record.updated_at or ""),
                reverse=table_sort_desc,
            )

            rows = [record.to_table_row() for record in filtered_records]
            if rows:
                st.caption("A tabla itt helyben szerkesztheto. A valtozasok az Alkalmaz gombbal irhatok vissza.")
                table_df = pd.DataFrame(rows)
                edited_df = st.data_editor(
                    table_df,
                    use_container_width=True,
                    hide_index=True,
                    key="records_table_editor",
                    disabled=["record_id", "created_at", "updated_at"],
                    column_config={
                        "entity_type": st.column_config.SelectboxColumn("entitas", options=ENTITY_OPTIONS, required=True),
                        "status": st.column_config.SelectboxColumn("statusz", options=STATUS_OPTIONS, required=True),
                        "decision_needed": st.column_config.CheckboxColumn("dontes?"),
                        "planning_bucket": st.column_config.SelectboxColumn("tervezesi hely", options=planning_options),
                        "next_step": st.column_config.TextColumn("next step"),
                        "next_step_estimate": st.column_config.TextColumn("becsult ido"),
                        "due_at": st.column_config.TextColumn("due date"),
                        "deadline": st.column_config.TextColumn("deadline"),
                        "start_at": st.column_config.TextColumn("start date"),
                        "record_id": st.column_config.TextColumn("record_id", disabled=True),
                        "created_at": st.column_config.TextColumn("created_at", disabled=True),
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
                                updated = update_record(updated, updated_at=utc_now_iso())
                                changed_count += 1
                            updated_lookup[record_id] = updated

                        merged_records = [updated_lookup.get(record.record_id, record) for record in records]
                        merged_records = sync_hierarchy_renames(merged_records, record_lookup)
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
                                history_events_path,
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
                        removed = delete_record(records_path, delete_id, history_path=history_events_path, source="ui")
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
                                history_events_path,
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
        st.caption("Drag-and-drop statuszvaltas task, case es project rekordokra. Az `inbox` itt `Backlog` neven jelenik meg.")
        with st.expander("Szűrés", expanded=False):
            filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
            kanban_project = filter_col1.text_input("Projekt", key="kanban_filter_project")
            kanban_org = filter_col2.text_input("Munkahely / organization", key="kanban_filter_org")
            kanban_status = filter_col3.selectbox("Státusz", [NONE_OPTION] + STATUS_OPTIONS, key="kanban_filter_status")
            kanban_entity = filter_col4.selectbox("Entitás", [NONE_OPTION, "task", "case", "project"], key="kanban_filter_entity")
            filter_col5, filter_col6, filter_col7 = st.columns(3)
            kanban_due_from = filter_col5.date_input("Due - tól", value=st.session_state.get("kanban_filter_due_from"), key="kanban_filter_due_from")
            kanban_due_to = filter_col6.date_input("Due - ig", value=st.session_state.get("kanban_filter_due_to"), key="kanban_filter_due_to")
            kanban_text = filter_col7.text_input("Szöveges szűrés", key="kanban_filter_text")

        task_like = [record for record in records if record.entity_type in {"task", "case", "project"}]
        if kanban_project.strip():
            needle = kanban_project.strip().lower()
            task_like = [record for record in task_like if needle in record.project.lower()]
        if kanban_org.strip():
            needle = kanban_org.strip().lower()
            task_like = [record for record in task_like if needle in record.organization.lower()]
        if kanban_status != NONE_OPTION:
            task_like = [record for record in task_like if record.status == kanban_status]
        if kanban_entity != NONE_OPTION:
            task_like = [record for record in task_like if record.entity_type == kanban_entity]
        if kanban_due_from:
            due_from_iso = kanban_due_from.isoformat()
            task_like = [record for record in task_like if record.due_at and record.due_at >= due_from_iso]
        if kanban_due_to:
            due_to_iso = kanban_due_to.isoformat()
            task_like = [record for record in task_like if record.due_at and record.due_at <= due_to_iso]
        if kanban_text.strip():
            needle = kanban_text.strip().lower()
            task_like = [
                record for record in task_like
                if needle in record.title.lower() or needle in record.summary.lower() or needle in record.case_name.lower()
            ]

        if not task_like:
            st.info("A jelenlegi szűrőkkel nincs megjeleníthető kanban elem.")
        else:
            kanban_payload = {
                "statuses": [{"key": status, "title": STATUS_LABELS.get(status, status)} for status in STATUS_OPTIONS],
                "items": [
                    {
                        "record_id": record.record_id,
                        "title": record.title,
                        "status": record.status,
                        "entity_type": record.entity_type,
                        "project": record.project,
                    }
                    for record in task_like
                ],
            }
            kanban_result = kanban_dnd_board(kanban_payload, key="kanban_dnd_surface")
            if isinstance(kanban_result, dict) and kanban_result.get("action") == "move_status":
                event_id = str(kanban_result.get("event_id", "")).strip()
                if event_id and st.session_state.get("last_kanban_drag_event") != event_id:
                    st.session_state["last_kanban_drag_event"] = event_id
                    record_id = str(kanban_result.get("record_id", "")).strip()
                    new_status = str(kanban_result.get("status", "")).strip()
                    moved_record = next((record for record in task_like if record.record_id == record_id), None)
                    if moved_record and new_status and new_status != moved_record.status:
                        persist_record(update_record(moved_record, status=new_status), source_dir, config, records_path, index_path, chroma_dir, history_events_path)

    with tab_timeline:
        render_gantt_view(records)

    with tab_execution:
        render_execution_graph(records, planning_layout, planning_layout_path, source_dir, config, records_path, index_path, chroma_dir, history_events_path)

    with tab_mindmap:
        st.subheader("Context Graph")
        if not records:
            st.info("Meg nincs megjelenitheto rekord.")
        else:
            st.markdown(
                """
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 12px 0;">
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#eff6ff;"><span style="width:18px;height:18px;border-radius:999px;background:#BFDBFE;border:1px solid #4B5563;display:inline-block;"></span><span>Organization</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#ecfeff;"><span style="width:18px;height:18px;background:#67E8F9;border:1px solid #4B5563;display:inline-block;clip-path:polygon(25% 0%,75% 0%,100% 50%,75% 100%,25% 100%,0% 50%);"></span><span>Team</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#f0fdf4;"><span style="width:18px;height:18px;border-radius:4px;background:#86EFAC;border:1px solid #4B5563;display:inline-block;"></span><span>Project</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#fdf2f8;"><span style="width:18px;height:18px;background:#F9A8D4;border:1px solid #4B5563;display:inline-block;clip-path:polygon(0 15%,80% 15%,80% 0,100% 50%,80% 100%,80% 85%,0 85%);"></span><span>Case</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#fefce8;"><span style="width:18px;height:18px;background:#FDE68A;border:1px solid #4B5563;display:inline-block;clip-path:polygon(0 25%,65% 25%,65% 0,100% 50%,65% 100%,65% 75%,0 75%);"></span><span>Task</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#fff1f2;"><span style="width:18px;height:18px;background:#FDA4AF;border:1px solid #4B5563;display:inline-block;transform:rotate(45deg);"></span><span>Decision</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#f5f3ff;"><span style="width:18px;height:18px;border-radius:4px;background:#C4B5FD;border:1px solid #4B5563;display:inline-block;"></span><span>Event</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#fff8e7;"><span style="width:18px;height:18px;background:#FFF8E7;border:1px solid #4B5563;display:inline-block;box-shadow:3px 3px 0 rgba(75,85,99,0.25);"></span><span>Person</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#fafaf9;"><span style="width:18px;height:18px;background:#F5F5F4;border:1px solid #4B5563;display:inline-block;clip-path:polygon(0 0,100% 0,100% 78%,66% 78%,66% 100%,50% 78%,0 78%);"></span><span>Note</span></div>
                  <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:12px;background:#f8fafc;"><span style="width:18px;height:18px;background:#CBD5E1;border:1px solid #4B5563;display:inline-block;clip-path:polygon(0 0,78% 0,78% 20%,100% 20%,100% 100%,0 100%);"></span><span>Source item</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.expander("Szűrés", expanded=False):
                filter_col1, filter_col2, filter_col3 = st.columns(3)
                filter_entities = filter_col1.multiselect(
                    "Entitas szures",
                    options=ENTITY_OPTIONS,
                    default=st.session_state.get("context_graph_entities", []),
                    key="context_graph_entities",
                )
                filter_statuses = filter_col2.multiselect(
                    "Statusz szures",
                    options=STATUS_OPTIONS,
                    default=st.session_state.get("context_graph_statuses", []),
                    key="context_graph_statuses",
                )
                active_only = filter_col3.checkbox("Csak aktivak", value=st.session_state.get("context_graph_active_only", True), key="context_graph_active_only")

                filter_col4, filter_col5, filter_col6, filter_col7 = st.columns(4)
                context_project = filter_col4.text_input("Projekt szures", key="context_graph_project")
                context_case = filter_col5.text_input("Ugy szures", key="context_graph_case")
                context_due_from = filter_col6.date_input("Due - tol", value=st.session_state.get("context_graph_due_from"), key="context_graph_due_from")
                context_due_to = filter_col7.date_input("Due - ig", value=st.session_state.get("context_graph_due_to"), key="context_graph_due_to")

                toggle_col1, toggle_col2 = st.columns(2)
                show_relations = toggle_col1.checkbox("Relaciok mutatasa", value=st.session_state.get("context_graph_show_relations", True), key="context_graph_show_relations")
                show_only_hierarchy = toggle_col2.checkbox("Csak hierarchia", value=st.session_state.get("context_graph_only_hierarchy", False), key="context_graph_only_hierarchy")
                graph_mode = st.selectbox(
                    "Nezet",
                    options=["branch_right", "radial"],
                    format_func=lambda value: {"branch_right": "Jobb fele agszerkezet", "radial": "Radial"}.get(value, value),
                    key="context_graph_mode",
                )

            filtered_graph_records = filter_context_graph_records(
                records,
                filter_entities,
                filter_statuses,
                context_project,
                context_case,
                active_only,
                context_due_from,
                context_due_to,
            )
            visible_graph_records = expand_context_graph_with_ancestors(records, filtered_graph_records)

            if not filtered_graph_records:
                st.info("A szurok mellett nincs megjelenitheto rekord.")
            else:
                selected_record_id = st.session_state.get("context_graph_selected_record_id")
                visible_ids = {record.record_id for record in visible_graph_records}
                if selected_record_id not in visible_ids:
                    selected_record_id = filtered_graph_records[0].record_id
                    st.session_state["context_graph_selected_record_id"] = selected_record_id

                panel_height = 700
                graph_col, editor_col = st.columns([2.2, 1.1])
                with graph_col:
                    graph_box = st.container(height=panel_height, border=True)
                    with graph_box:
                        graph_payload = build_context_graph_payload(
                            visible_graph_records,
                            selected_record_id,
                            show_relations,
                            show_only_hierarchy,
                            graph_mode,
                            st.session_state.get("context_graph_force_fit_token", ""),
                        )
                        graph_result = context_graph(graph_payload, key="context_graph_surface")
                        if isinstance(graph_result, dict) and graph_result.get("action") in {"select_node", "reparent_node"}:
                            event_id = str(graph_result.get("event_id", "")).strip()
                            if not event_id or st.session_state.get("last_context_graph_event") != event_id:
                                if event_id:
                                    st.session_state["last_context_graph_event"] = event_id
                                if graph_result.get("action") == "select_node":
                                    record_id = str(graph_result.get("record_id", "")).strip()
                                    if record_id and record_id in {record.record_id for record in records}:
                                        st.session_state["context_graph_selected_record_id"] = record_id
                                        st.rerun()
                                if graph_result.get("action") == "reparent_node":
                                    moved_id = str(graph_result.get("record_id", "")).strip()
                                    new_parent_id = str(graph_result.get("target_record_id", "")).strip()
                                    if (
                                        moved_id
                                        and new_parent_id
                                        and moved_id != new_parent_id
                                        and moved_id in {record.record_id for record in records}
                                        and new_parent_id in {record.record_id for record in records}
                                        and not is_descendant(records, moved_id, new_parent_id)
                                    ):
                                        st.session_state["context_graph_selected_record_id"] = moved_id
                                        updated_records = reparent_subtree(records, moved_id, new_parent_id)
                                        persist_records_bulk(
                                            updated_records,
                                            source_dir,
                                            config,
                                            records_path,
                                            index_path,
                                            chroma_dir,
                                            history_events_path,
                                            "Context Graph hierarchia frissitve.",
                                        )

                with editor_col:
                    selected_record = next((record for record in records if record.record_id == st.session_state.get("context_graph_selected_record_id")), None)
                    editor_box = st.container(height=panel_height, border=True)
                    with editor_box:
                        if selected_record is None:
                            st.info("Valassz ki egy rekordot a grafon.")
                        else:
                            st.caption(f"Kijelolt rekord: {record_label(selected_record)}")
                            action_placeholder = st.empty()
                            edit_values = render_record_editor(
                                f"context_graph_edit_{selected_record.record_id}",
                                records,
                                existing_values,
                                planning_options,
                                planning_titles,
                                base_record=selected_record,
                                allow_parent_edit=False,
                            )
                            with action_placeholder.container():
                                action_col1, action_col2, action_col3, action_col4, action_col5, action_col6 = st.columns(6)
                                if action_col1.button("Mentés", key="save_context_graph_record"):
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
                                        relations=graph_edges_to_relations(edit_values["graph_edges"]),
                                        graph_edges=edit_values["graph_edges"],
                                        decision_needed=edit_values["decision_needed"],
                                        decision_context=edit_values["decision_context"],
                                        start_at=edit_values["start_at"],
                                        due_at=edit_values["due_at"],
                                        deadline=edit_values["deadline"],
                                        event_at=edit_values["event_at"],
                                        next_step=edit_values["next_step"],
                                        next_step_estimate=edit_values["next_step_estimate"],
                                        next_steps=edit_values["next_steps"],
                                        planning_bucket=edit_values["planning_bucket"],
                                        focus_rank=edit_values["focus_rank"],
                                    )
                                    persist_record(with_synced_hierarchy_title(updated_record), source_dir, config, records_path, index_path, chroma_dir, history_events_path)
                                if action_col2.button("Add child", key="add_child_context_graph"):
                                    child_record = KnowledgeRecord(
                                        record_id=build_record_id("Uj child"),
                                        title="Uj child",
                                        summary="",
                                        content="",
                                        source_type="manual",
                                        entity_type="note",
                                        status="inbox",
                                        organization=selected_record.organization,
                                        team=selected_record.team,
                                        project=selected_record.project,
                                        case_name=selected_record.case_name,
                                        parent_id=selected_record.record_id,
                                    )
                                    st.session_state["context_graph_selected_record_id"] = child_record.record_id
                                    persist_record(child_record, source_dir, config, records_path, index_path, chroma_dir, history_events_path)
                                if action_col3.button("Add sibling", key="add_sibling_context_graph"):
                                    sibling_record = KnowledgeRecord(
                                        record_id=build_record_id("Uj sibling"),
                                        title="Uj sibling",
                                        summary="",
                                        content="",
                                        source_type="manual",
                                        entity_type="note",
                                        status="inbox",
                                        organization=selected_record.organization,
                                        team=selected_record.team,
                                        project=selected_record.project,
                                        case_name=selected_record.case_name,
                                        parent_id=selected_record.parent_id,
                                    )
                                    st.session_state["context_graph_selected_record_id"] = sibling_record.record_id
                                    persist_record(sibling_record, source_dir, config, records_path, index_path, chroma_dir, history_events_path)
                                if action_col4.button("Részlet", key="open_from_context_graph"):
                                    st.session_state["selected_record_id"] = selected_record.record_id
                                    st.rerun()
                                if action_col5.button("History", key="open_history_context_graph"):
                                    st.session_state[f"context_history_{selected_record.record_id}"] = not st.session_state.get(f"context_history_{selected_record.record_id}", False)
                                if action_col6.button("Törlés", key="delete_context_graph_record"):
                                    updated_records = remove_record_and_reparent_children(records, selected_record.record_id)
                                    if updated_records == records:
                                        st.warning("A rekord nem található vagy nem törölhető.")
                                    else:
                                        remaining = [record for record in updated_records if record.record_id != selected_record.record_id]
                                        if remaining:
                                            st.session_state["context_graph_selected_record_id"] = remaining[0].record_id
                                        persist_records_bulk(
                                            updated_records,
                                            source_dir,
                                            config,
                                            records_path,
                                            index_path,
                                            chroma_dir,
                                            history_events_path,
                                            "Context Graph rekord törölve.",
                                        )
                            with st.expander("Markdown hierarchy import", expanded=False):
                                markdown_import_raw = st.text_area(
                                    "Markdown fa beillesztése a kijelölt node alá",
                                    value="",
                                    height=180,
                                    help="A listaelemek külön node-okká alakulnak. A '- [ ]' és '- [x]' sorokból task node készül.",
                                    key=f"context_markdown_import_{selected_record.record_id}",
                                )
                                if st.button("Import hierarchy", key=f"context_markdown_import_button_{selected_record.record_id}"):
                                    imported_records = build_records_from_markdown_import(selected_record, markdown_import_raw)
                                    if not imported_records:
                                        st.warning("Nem találtam importálható markdown-hierarchiát.")
                                    else:
                                        st.session_state["context_graph_selected_record_id"] = imported_records[0].record_id
                                        persist_records_bulk(
                                            records + imported_records,
                                            source_dir,
                                            config,
                                            records_path,
                                            index_path,
                                            chroma_dir,
                                            history_events_path,
                                            f"Markdown hierarchy import kész: {len(imported_records)} új node.",
                                        )
                            st.markdown("**Kapcsolatok itt helyben**")
                            relation_records = [record for record in records if record.record_id != selected_record.record_id]
                            current_edges = normalize_graph_edges(selected_record.graph_edges, selected_record.relations, selected_record.record_id)
                            if current_edges:
                                for index, edge in enumerate(current_edges):
                                    target_id = edge.get("target_id", "")
                                    target_record = next((record for record in records if record.record_id == target_id), None)
                                    edge_title = record_label(target_record) if target_record else target_id
                                    edge_type = edge.get("relation_type", "related_to")
                                    edge_label = edge.get("label", "")
                                    row_col1, row_col2 = st.columns([6, 1])
                                    row_col1.caption(
                                        f"{edge_type} -> {edge_title}" + (f" | {edge_label}" if edge_label else "")
                                    )
                                    if row_col2.button("×", key=f"context_remove_relation_{selected_record.record_id}_{index}"):
                                        updated_edges = [item for item_index, item in enumerate(current_edges) if item_index != index]
                                        updated_record = update_record(
                                            selected_record,
                                            graph_edges=updated_edges,
                                            relations=graph_edges_to_relations(updated_edges),
                                        )
                                        persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir, history_events_path)
                            else:
                                st.caption("Ehhez a rekordhoz még nincs külön graph-kapcsolat.")

                            add_relation_col1, add_relation_col2 = st.columns(2)
                            relation_target_id = add_relation_col1.selectbox(
                                "Kapcsolat célpont",
                                options=[""] + [record.record_id for record in relation_records],
                                index=0,
                                format_func=lambda record_id: "Valassz rekordot"
                                if not record_id
                                else record_label(next(record for record in relation_records if record.record_id == record_id)),
                                key=f"context_relation_target_{selected_record.record_id}",
                            )
                            relation_type = add_relation_col2.selectbox(
                                "Kapcsolat típusa",
                                options=RELATION_TYPE_OPTIONS,
                                key=f"context_relation_type_{selected_record.record_id}",
                            )
                            relation_label = st.text_input(
                                "Kapcsolat címke",
                                value="",
                                placeholder="opcionalis",
                                key=f"context_relation_label_{selected_record.record_id}",
                            )
                            if st.button("Add relation", key=f"add_relation_context_graph_{selected_record.record_id}"):
                                if not relation_target_id:
                                    st.warning("Valassz ki egy cel rekordot a kapcsolathoz.")
                                else:
                                    next_edges = normalize_graph_edges(
                                        current_edges
                                        + [
                                            {
                                                "target_id": relation_target_id,
                                                "relation_type": relation_type,
                                                "label": relation_label.strip(),
                                            }
                                        ],
                                        self_id=selected_record.record_id,
                                    )
                                    updated_record = update_record(
                                        selected_record,
                                        graph_edges=next_edges,
                                        relations=graph_edges_to_relations(next_edges),
                                    )
                                    persist_record(updated_record, source_dir, config, records_path, index_path, chroma_dir, history_events_path)
                            if st.session_state.get(f"context_history_{selected_record.record_id}", False):
                                render_record_history(history_events_path, selected_record.record_id, key_prefix=f"context_{selected_record.record_id}")

                st.caption("Phase A: szurheto, kattinthato es ugyanazon a tabon szerkesztheto Context Graph. A teljes vizualis edge-szerkesztes es a kulon Graph nezet a kovetkezo fazisba tartozik.")

    with tab_search:
        st.subheader("Kereses")
        with st.form("search_form", clear_on_submit=False):
            query = st.text_input("Kerdes vagy kulcsszo", key="search_query")
            submitted = st.form_submit_button("Kereses inditasa")
        if submitted:
            chunks = load_index(index_path)
            st.session_state["search_results"] = search_chunks(chunks, query, limit=10)
        results = st.session_state.get("search_results", [])
        if not results and query:
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
                if chunk.record_id:
                    action_col1, action_col2, action_col3 = st.columns(3)
                    if action_col1.button("Megnyit részletben", key=f"open_search_detail_{chunk.record_id}_{chunk.chunk_id}"):
                        st.session_state["selected_record_id"] = chunk.record_id
                        st.rerun()
                    if action_col2.button("Kijelölés Context Graphban", key=f"open_search_context_{chunk.record_id}_{chunk.chunk_id}"):
                        st.session_state["selected_record_id"] = chunk.record_id
                        st.session_state["context_graph_selected_record_id"] = chunk.record_id
                        st.session_state["context_graph_force_fit_token"] = utc_now_iso()
                        st.session_state["pending_main_tab"] = "Context Graph"
                        st.rerun()
                    if action_col3.button("Kijelölés Executionben", key=f"open_search_execution_{chunk.record_id}_{chunk.chunk_id}"):
                        st.session_state["selected_record_id"] = chunk.record_id
                        st.session_state["execution_selected_record_id"] = chunk.record_id
                        matching_record = next((record for record in records if record.record_id == chunk.record_id), None)
                        if matching_record and matching_record.planning_bucket:
                            st.session_state["pending_execution_filter_bucket"] = matching_record.planning_bucket
                        st.session_state["pending_main_tab"] = "Execution Graph"
                        st.rerun()
                st.divider()


if __name__ == "__main__":
    app()
