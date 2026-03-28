from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path


SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or f"node-{uuid.uuid4().hex[:6]}"


def _week_title(start: date) -> str:
    end = start + timedelta(days=6)
    return f"{start.isoformat()} - {end.isoformat()}"


def _default_week() -> dict:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return {
        "key": f"week-{week_start.isoformat()}",
        "title": _week_title(week_start),
        "start_date": week_start.isoformat(),
        "days": [
            {
                "key": f"day-{today.isoformat()}",
                "title": today.strftime("%A"),
                "date": today.isoformat(),
                "blocks": [
                    {"key": f"block-{today.isoformat()}-must", "title": "Mindenkepp", "lane": "must"},
                    {"key": f"block-{today.isoformat()}-prefer", "title": "Lehetoleg", "lane": "prefer"},
                ],
            }
        ],
    }


def default_layout() -> dict:
    return {"version": 2, "weeks": [_default_week()]}


def _migrate_legacy_layout(payload: dict) -> dict:
    weeks: list[dict] = []
    groups = payload.get("groups", [])
    fallback_week = {
        "key": "week-legacy",
        "title": "Atvett blokkok",
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
    if not path.exists():
        save_planning_layout(path, layout)
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
        "title": title.strip() if title else day_date.strftime("%A"),
        "date": day_date.isoformat(),
        "blocks": [
            {"key": f"{day_key}-must", "title": "Mindenkepp", "lane": "must"},
            {"key": f"{day_key}-prefer", "title": "Lehetoleg", "lane": "prefer"},
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


def day_for_bucket(layout: dict, bucket_key: str) -> dict | None:
    for bucket in iter_buckets(layout):
        if bucket.get("key") == bucket_key:
            return bucket
    return None
