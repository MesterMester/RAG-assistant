from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
import hashlib
import mailbox
import re
from pathlib import Path


MAILBOX_EXCLUDE_SUFFIXES = {
    ".msf",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".json",
    ".js",
    ".css",
    ".html",
    ".txt",
    ".md",
    ".bak",
    ".tmp",
    ".log",
}
PATH_LINE_RE = re.compile(r"^\s*(profile_root|mail_root|exclude_folder|since_days|max_messages_per_mailbox)\s*:\s*(.+?)\s*$", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


@dataclass(slots=True)
class ThunderbirdImportConfig:
    md_path: Path
    profile_root: Path | None
    mail_roots: list[Path]
    exclude_folders: list[str]
    since_days: int = 30
    max_messages_per_mailbox: int = 20


@dataclass(slots=True)
class ThunderbirdFolderRules:
    md_path: Path
    included_paths: list[str]
    excluded_paths: list[str]


@dataclass(slots=True)
class ThunderbirdMailboxInventory:
    path: str
    size_bytes: int
    account_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ThunderbirdMessagePreview:
    preview_id: str
    mailbox_path: str
    mailbox_name: str
    account_hint: str
    subject: str
    sender: str
    recipients: str
    sent_at: str
    message_id: str
    body_preview: str
    body_full: str
    selected: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_md_value(value: str) -> str:
    text = value.strip().strip("`").strip()
    link_match = MARKDOWN_LINK_RE.search(text)
    if link_match:
        return link_match.group(1).strip()
    return text


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _normalize_relative_mail_path(value: str) -> str:
    return value.strip().strip("`").strip().replace("\\", "/").strip("/")


def load_thunderbird_import_config(md_path: Path) -> tuple[ThunderbirdImportConfig | None, list[str]]:
    errors: list[str] = []
    if not md_path.exists():
        return None, [f"A Thunderbird config MD fájl nem található: {md_path}"]

    profile_root: Path | None = None
    mail_roots: list[Path] = []
    exclude_folders: list[str] = ["Trash", "Spam", "Junk", "Bin", "Deleted"]
    since_days = 30
    max_messages_per_mailbox = 20

    for raw_line in md_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PATH_LINE_RE.match(line)
        if match:
            key = match.group(1).lower()
            value = _clean_md_value(match.group(2))
            if key == "profile_root" and value:
                profile_root = Path(value).expanduser()
            elif key == "mail_root" and value:
                mail_roots.append(Path(value).expanduser())
            elif key == "exclude_folder" and value:
                exclude_folders.append(value)
            elif key == "since_days":
                since_days = _parse_int(value, since_days)
            elif key == "max_messages_per_mailbox":
                max_messages_per_mailbox = _parse_int(value, max_messages_per_mailbox)
            continue
        if line.startswith(("-", "*")):
            value = _clean_md_value(line[1:].strip())
            if value.startswith("/") or value.startswith("~"):
                mail_roots.append(Path(value).expanduser())

    resolved_roots: list[Path] = []
    if profile_root:
        if profile_root.name in {"ImapMail", "Mail"}:
            resolved_roots.append(profile_root)
        else:
            for child_name in ["ImapMail", "Mail"]:
                child = profile_root / child_name
                if child.exists():
                    resolved_roots.append(child)
    resolved_roots.extend(mail_roots)

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in resolved_roots:
        root_str = str(root.resolve()) if root.exists() else str(root)
        if root_str in seen:
            continue
        seen.add(root_str)
        unique_roots.append(root)

    if not unique_roots:
        errors.append("Nem találtam használható Thunderbird mail gyökeret. Adj meg `profile_root:` vagy `mail_root:` sort a config MD fájlban.")

    return (
        ThunderbirdImportConfig(
            md_path=md_path,
            profile_root=profile_root,
            mail_roots=unique_roots,
            exclude_folders=list(dict.fromkeys(exclude_folders)),
            since_days=since_days,
            max_messages_per_mailbox=max_messages_per_mailbox,
        ),
        errors,
    )


def load_thunderbird_folder_rules(md_path: Path | None) -> tuple[ThunderbirdFolderRules | None, list[str]]:
    if md_path is None:
        return ThunderbirdFolderRules(md_path=Path(""), included_paths=[], excluded_paths=[]), []
    errors: list[str] = []
    if not md_path.exists():
        return None, [f"A Thunderbird folders MD fájl nem található: {md_path}"]

    section = ""
    included: list[str] = []
    excluded: list[str] = []
    for raw_line in md_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = line.lower().strip("# ").strip()
        if heading == "included paths":
            section = "included"
            continue
        if heading == "excluded paths":
            section = "excluded"
            continue
        if not line.startswith(("-", "*")):
            continue
        value = _normalize_relative_mail_path(_clean_md_value(line[1:].strip()))
        if not value:
            continue
        if section == "included":
            included.append(value)
        elif section == "excluded":
            excluded.append(value)

    return ThunderbirdFolderRules(md_path=md_path, included_paths=included, excluded_paths=excluded), errors


def _is_mailbox_candidate(path: Path, exclude_folders: list[str]) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return False
    if path.suffix.lower() in MAILBOX_EXCLUDE_SUFFIXES:
        return False
    if any(part in exclude_folders for part in path.parts):
        return False
    sibling_msf = path.parent / f"{path.name}.msf"
    if sibling_msf.exists():
        return True
    if path.suffix:
        return False
    return path.stat().st_size > 0


def _relative_mailbox_path(path: Path, config: ThunderbirdImportConfig, root: Path) -> str:
    try:
        if config.profile_root and path.is_relative_to(config.profile_root):
            return path.relative_to(config.profile_root).as_posix()
    except Exception:
        pass
    try:
        return f"{root.name}/{path.relative_to(root).as_posix()}".strip("/")
    except Exception:
        return path.name


def _matches_folder_rules(relative_path: str, rules: ThunderbirdFolderRules | None) -> bool:
    if not rules:
        return True
    normalized = _normalize_relative_mail_path(relative_path)
    if rules.included_paths:
        if not any(normalized == item or normalized.endswith(f"/{item}") for item in rules.included_paths):
            return False
    if rules.excluded_paths:
        if any(normalized == item or normalized.endswith(f"/{item}") for item in rules.excluded_paths):
            return False
    return True


def discover_mailboxes(config: ThunderbirdImportConfig, rules: ThunderbirdFolderRules | None = None) -> tuple[list[ThunderbirdMailboxInventory], list[str]]:
    inventory: list[ThunderbirdMailboxInventory] = []
    errors: list[str] = []
    for root in config.mail_roots:
        if not root.exists():
            errors.append(f"Mail root nem található: {root}")
            continue
        for path in root.rglob("*"):
            try:
                if not _is_mailbox_candidate(path, config.exclude_folders):
                    continue
                relative_path = _relative_mailbox_path(path, config, root)
                if not _matches_folder_rules(relative_path, rules):
                    continue
                account_hint = path.relative_to(root).parts[0] if path != root and path.relative_to(root).parts else root.name
                inventory.append(
                    ThunderbirdMailboxInventory(
                        path=str(path),
                        size_bytes=path.stat().st_size,
                        account_hint=account_hint,
                    )
                )
            except Exception as exc:
                errors.append(f"Hiba mailbox felderítés közben: {path} | {exc}")
    inventory.sort(key=lambda item: (item.account_hint.lower(), item.path.lower()))
    return inventory, errors


def _message_datetime(message: Message) -> datetime | None:
    raw_date = message.get("date", "").strip()
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode_payload(payload: bytes | str | None, charset: str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    for encoding in [charset, "utf-8", "latin-1"]:
        if not encoding:
            continue
        try:
            return payload.decode(encoding, errors="replace")
        except LookupError:
            continue
    return payload.decode("utf-8", errors="replace")


def extract_text_body(message: Message) -> str:
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type != "text/plain":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            parts.append(_decode_payload(payload, part.get_content_charset()))
        return "\n\n".join(part.strip() for part in parts if part.strip())
    payload = message.get_payload(decode=True)
    return _decode_payload(payload, message.get_content_charset()).strip()


def _preview_id(mailbox_path: Path, message: Message, sent_at: str) -> str:
    fingerprint = "|".join(
        [
            str(mailbox_path),
            message.get("message-id", "").strip(),
            message.get("subject", "").strip(),
            sent_at,
        ]
    )
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def preview_messages(inventory: list[ThunderbirdMailboxInventory], since_days: int, max_messages_per_mailbox: int) -> tuple[list[ThunderbirdMessagePreview], list[str]]:
    previews: list[ThunderbirdMessagePreview] = []
    errors: list[str] = []
    min_dt = datetime.now(timezone.utc) - timedelta(days=max(0, since_days))

    for mailbox_item in inventory:
        mailbox_path = Path(mailbox_item.path)
        try:
            mbox = mailbox.mbox(str(mailbox_path), create=False)
        except Exception as exc:
            errors.append(f"Nem sikerült megnyitni a mailboxot: {mailbox_path} | {exc}")
            continue

        collected: list[ThunderbirdMessagePreview] = []
        try:
            for message in mbox:
                sent_dt = _message_datetime(message)
                if sent_dt and sent_dt < min_dt:
                    continue
                body = extract_text_body(message)
                body_preview = re.sub(r"\s+", " ", body).strip()[:240]
                collected.append(
                    ThunderbirdMessagePreview(
                        preview_id=_preview_id(mailbox_path, message, sent_dt.isoformat() if sent_dt else ""),
                        mailbox_path=str(mailbox_path),
                        mailbox_name=mailbox_path.name,
                        account_hint=mailbox_item.account_hint,
                        subject=message.get("subject", "").strip() or "(tárgy nélkül)",
                        sender=message.get("from", "").strip(),
                        recipients=message.get("to", "").strip(),
                        sent_at=(sent_dt.isoformat() if sent_dt else ""),
                        message_id=message.get("message-id", "").strip(),
                        body_preview=body_preview,
                        body_full=body,
                    )
                )
        except Exception as exc:
            errors.append(f"Hiba üzenetbeolvasás közben: {mailbox_path} | {exc}")
            continue

        collected.sort(key=lambda item: item.sent_at or "", reverse=True)
        previews.extend(collected[: max(1, max_messages_per_mailbox)])

    previews.sort(key=lambda item: item.sent_at or "", reverse=True)
    return previews, errors


def thunderbird_preview_rows(previews: list[ThunderbirdMessagePreview]) -> list[dict]:
    return [
        {
            "selected": item.selected,
            "preview_id": item.preview_id,
            "subject": item.subject,
            "from": item.sender,
            "to": item.recipients,
            "sent_at": item.sent_at,
            "mailbox": item.mailbox_name,
            "account": item.account_hint,
            "message_id": item.message_id,
            "body_preview": item.body_preview,
        }
        for item in previews
    ]
