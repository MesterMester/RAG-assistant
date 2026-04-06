from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path


SLUG_RE = re.compile(r"[^a-z0-9]+")
WEEKDAY_TITLES = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or f"node-{uuid.uuid4().hex[:6]}"


def _week_title(start: date) -> str:
    end = start + timedelta(days=6)
    return f"{start.isoformat()} - {end.isoformat()}"


def _weekday_title(day_value: date) -> str:
    return WEEKDAY_TITLES[day_value.weekday()]


def _default_day(day_value: date, include_current_blocks: bool = False) -> dict:
    day_key = f"day-{day_value.isoformat()}"
    blocks = [
        {"key": f"{day_key}-must", "title": "Mindenképp", "lane": "must"},
        {"key": f"{day_key}-prefer", "title": "Lehetőleg", "lane": "prefer"},
    ]
    if include_current_blocks:
        blocks = [
            {"key": f"{day_key}-focus", "title": "Fő fókusz", "lane": "focus"},
            {"key": f"{day_key}-must", "title": "Mindenképp", "lane": "must"},
            {"key": f"{day_key}-prefer", "title": "Lehetőleg", "lane": "prefer"},
        ]
    return {
        "key": day_key,
        "title": _weekday_title(day_value),
        "custom_title": False,
        "date": day_value.isoformat(),
        "blocks": blocks,
    }


def _default_week(start_day: date, include_today_structure: bool = False) -> dict:
    days = []
    for offset in range(7):
        day_value = start_day + timedelta(days=offset)
        days.append(_default_day(day_value, include_current_blocks=include_today_structure and offset == date.today().weekday()))
    return {
        "key": f"week-{start_day.isoformat()}",
        "title": _week_title(start_day),
        "start_date": start_day.isoformat(),
        "days": days,
    }


def default_layout() -> dict:
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    next_week_start = current_week_start + timedelta(days=7)
    return {
        "version": 2,
        "weeks": [
            _default_week(current_week_start, include_today_structure=True),
            _default_week(next_week_start, include_today_structure=False),
        ],
    }


def _migrate_legacy_layout(payload: dict) -> dict:
    weeks: list[dict] = []
    groups = payload.get("groups", [])
    fallback_week = {
        "key": "week-legacy",
        "title": "Átvett blokkok",
        "start_date": None,
        "days": [],
    }
    for group in groups:
        group_key = group.get("key", slugify(group.get("title", "day")))
        day = {
            "key": f"day-{group_key}",
            "title": group.get("title", group_key),
            "date": None,
            "blocks": [],
        }
        for bucket in group.get("buckets", []):
            day["blocks"].append(
                {
                    "key": bucket.get("key", slugify(bucket.get("title", "block"))),
                    "title": bucket.get("title", "Blokk"),
                    "lane": bucket.get("lane", "session"),
                }
            )
        fallback_week["days"].append(day)
    if fallback_week["days"]:
        weeks.append(fallback_week)
    return {"version": 2, "weeks": weeks or default_layout()["weeks"]}


def load_planning_layout(path: Path) -> dict:
    if not path.exists():
        return default_layout()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "weeks" not in payload:
        payload = _migrate_legacy_layout(payload)
    payload.setdefault("version", 2)
    payload.setdefault("weeks", [])
    return payload


def save_planning_layout(path: Path, layout: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_layout(path: Path) -> dict:
    layout = load_planning_layout(path)
    layout = ensure_standard_weeks(layout)
    layout = ensure_focus_block(layout)
    layout = normalize_layout_labels(layout)
    if not path.exists():
        save_planning_layout(path, layout)
    return layout


def ensure_focus_block(layout: dict) -> dict:
    today_iso = date.today().isoformat()
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            if day.get("date") != today_iso:
                continue
            blocks = day.setdefault("blocks", [])
            has_focus = any((block.get("lane", "session") == "focus") for block in blocks)
            if not has_focus:
                day_key = day.get("key", f"day-{today_iso}")
                blocks.insert(0, {"key": f"{day_key}-focus", "title": "Fő fókusz", "lane": "focus"})
            return layout
    return layout


def normalize_layout_labels(layout: dict) -> dict:
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            day_date_raw = day.get("date")
            if day_date_raw and not day.get("custom_title"):
                day["title"] = _weekday_title(date.fromisoformat(day_date_raw))
            for block in day.get("blocks", []):
                lane = block.get("lane", "session")
                if lane == "focus":
                    block["title"] = "Fő fókusz"
                elif lane == "must":
                    block["title"] = "Mindenképp"
                elif lane == "prefer":
                    block["title"] = "Lehetőleg"
    return layout


def ensure_standard_weeks(layout: dict) -> dict:
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    starts = {week.get("start_date") for week in layout.get("weeks", [])}
    for offset in range(-1, 10):
        week_start = current_week_start + timedelta(days=7 * offset)
        if week_start.isoformat() not in starts:
            layout.setdefault("weeks", []).append(_default_week(week_start, include_today_structure=(offset == 0)))
    layout["weeks"].sort(key=lambda week: week.get("start_date") or "9999-99-99")
    return layout


def iter_buckets(layout: dict) -> list[dict]:
    buckets: list[dict] = []
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            for block in day.get("blocks", []):
                enriched = dict(block)
                enriched["week_key"] = week.get("key", "")
                enriched["week_title"] = week.get("title", "")
                enriched["day_key"] = day.get("key", "")
                enriched["day_title"] = day.get("title", "")
                enriched["day_date"] = day.get("date")
                buckets.append(enriched)
    return buckets


def layout_rows(layout: dict) -> list[dict]:
    rows: list[dict] = []
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            for block in day.get("blocks", []):
                rows.append(
                    {
                        "week": week.get("title", ""),
                        "day": day.get("title", ""),
                        "date": day.get("date") or "",
                        "block": block.get("title", ""),
                        "bucket_key": block.get("key", ""),
                    }
                )
    return rows


def add_week(layout: dict, start_date_value: str | None = None, title: str | None = None) -> dict:
    start = date.fromisoformat(start_date_value) if start_date_value else date.today()
    key = f"week-{start.isoformat()}-{uuid.uuid4().hex[:4]}"
    layout.setdefault("weeks", []).append(
        {
            "key": key,
            "title": title.strip() if title else _week_title(start),
            "custom_title": bool(title and title.strip()),
            "start_date": start.isoformat(),
            "days": [],
        }
    )
    return layout


def add_day(layout: dict, week_key: str, day_date_value: str, title: str | None = None) -> dict:
    day_date = date.fromisoformat(day_date_value)
    day_key = f"day-{day_date.isoformat()}-{uuid.uuid4().hex[:4]}"
    day = {
        "key": day_key,
        "title": title.strip() if title else _weekday_title(day_date),
        "custom_title": bool(title and title.strip()),
        "date": day_date.isoformat(),
        "blocks": [
            {"key": f"{day_key}-must", "title": "Mindenképp", "lane": "must"},
            {"key": f"{day_key}-prefer", "title": "Lehetőleg", "lane": "prefer"},
        ],
    }
    for week in layout.get("weeks", []):
        if week.get("key") == week_key:
            week.setdefault("days", []).append(day)
            week["days"].sort(key=lambda item: item.get("date") or "9999-99-99")
            break
    return layout


def add_block(layout: dict, day_key: str, title: str, lane: str = "session") -> dict:
    block_key = f"{day_key}-{slugify(title)}-{uuid.uuid4().hex[:4]}"
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            if day.get("key") == day_key:
                day.setdefault("blocks", []).append({"key": block_key, "title": title.strip(), "lane": lane})
                break
    return layout


def move_block_to_day(layout: dict, block_key: str, target_day_key: str) -> dict:
    moved_block: dict | None = None
    source_day_key = ""
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            if day.get("key") == target_day_key:
                continue
            kept_blocks: list[dict] = []
            for block in day.get("blocks", []):
                if block.get("key") == block_key:
                    moved_block = dict(block)
                    source_day_key = day.get("key", "")
                else:
                    kept_blocks.append(block)
            day["blocks"] = kept_blocks
    if not moved_block or source_day_key == target_day_key:
        return layout
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            if day.get("key") == target_day_key:
                day.setdefault("blocks", []).append(moved_block)
                return layout
    return layout


def remove_week(layout: dict, week_key: str) -> dict:
    layout["weeks"] = [week for week in layout.get("weeks", []) if week.get("key") != week_key]
    return layout


def remove_day(layout: dict, day_key: str) -> dict:
    for week in layout.get("weeks", []):
        week["days"] = [day for day in week.get("days", []) if day.get("key") != day_key]
    layout["weeks"] = [week for week in layout.get("weeks", []) if week.get("days")]
    return layout


def remove_block(layout: dict, block_key: str) -> dict:
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            day["blocks"] = [block for block in day.get("blocks", []) if block.get("key") != block_key]
    for week in layout.get("weeks", []):
        week["days"] = [day for day in week.get("days", []) if day.get("blocks")]
    layout["weeks"] = [week for week in layout.get("weeks", []) if week.get("days")]
    return layout


def find_day(layout: dict, day_key: str) -> dict | None:
    for week in layout.get("weeks", []):
        for day in week.get("days", []):
            if day.get("key") == day_key:
                return day
    return None


def rename_day(layout: dict, day_key: str, title: str) -> dict:
    day = find_day(layout, day_key)
    if day:
        day["title"] = title.strip()
        day["custom_title"] = True
    return layout


def rename_week(layout: dict, week_key: str, title: str) -> dict:
    for week in layout.get("weeks", []):
        if week.get("key") == week_key:
            week["title"] = title.strip()
            week["custom_title"] = True
            break
    return layout


def find_block(layout: dict, block_key: str) -> dict | None:
    for day in [day for week in layout.get("weeks", []) for day in week.get("days", [])]:
        for block in day.get("blocks", []):
            if block.get("key") == block_key:
                enriched = dict(block)
                enriched["day_key"] = day.get("key", "")
                enriched["day_date"] = day.get("date")
                return enriched
    return None


def must_bucket_for_day(layout: dict, day_key: str) -> str | None:
    day = find_day(layout, day_key)
    if not day:
        return None
    for block in day.get("blocks", []):
        if block.get("lane") == "must":
            return block.get("key")
    return None


def day_for_bucket(layout: dict, bucket_key: str) -> dict | None:
    for bucket in iter_buckets(layout):
        if bucket.get("key") == bucket_key:
            return bucket
    return None
