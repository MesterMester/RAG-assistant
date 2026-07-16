from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
import hashlib
import json
import mailbox
import re
import sqlite3
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
TAG_PREF_RE = re.compile(r'user_pref\("mailnews\.tags\.([^"]+)\.tag",\s*"((?:\\.|[^"])*)"\);')
DEFAULT_THUNDERBIRD_LABELS = {
    "$label1": "Important",
    "$label2": "Work",
    "$label3": "Personal",
    "$label4": "To Do",
    "$label5": "Later",
}
TAG_HEADER_NAMES = ["keywords", "x-keywords", "x-mozilla-keys", "x-mozilla-label", "x-label", "x-tag", "x-tags"]
MSF_FIELD_RE = re.compile(r"\^([0-9A-Fa-f]+)(?:=([^\^\]\)\r\n]*)|\^([0-9A-Fa-f]+))")
MSF_ATOM_RE = re.compile(r"\(([0-9A-Fa-f]+)=((?:\\.|[^)])*)\)")
MESSAGE_ID_RE = re.compile(r"<[^<>\s]+@[^<>\s]+>")


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
    cc: str
    sent_at: str
    message_id: str
    thunderbird_tags: str
    thunderbird_tag_headers: str
    has_attachment: bool
    in_reply_to: str
    references: str
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


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def decoded_message_header(message: Message, name: str) -> str:
    return decode_header_value(message.get(name, ""))


def _decode_js_pref_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.encode("utf-8", errors="replace").decode("unicode_escape", errors="replace")


def _candidate_profile_roots(config: ThunderbirdImportConfig) -> list[Path]:
    candidates: list[Path] = []
    if config.profile_root:
        candidates.append(config.profile_root)
    for mail_root in config.mail_roots:
        candidates.append(mail_root)
        if mail_root.name in {"ImapMail", "Mail"}:
            candidates.append(mail_root.parent)
        candidates.extend(list(mail_root.parents)[:4])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate.expanduser())
    return unique


def load_thunderbird_tag_map(config: ThunderbirdImportConfig) -> tuple[dict[str, str], list[str]]:
    """Load Thunderbird tag display names from prefs.js.

    Mbox messages usually store tag keys in X-Mozilla-Keys. The human-readable
    names live in the Thunderbird profile prefs.js as mailnews.tags.<key>.tag.
    """
    errors: list[str] = []
    tag_map: dict[str, str] = {}
    prefs_candidates = [root / "prefs.js" for root in _candidate_profile_roots(config)]

    for prefs_path in prefs_candidates:
        if not prefs_path.exists():
            continue
        try:
            text = prefs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"Nem sikerült olvasni a Thunderbird prefs.js fájlt: {prefs_path} | {exc}")
            continue
        for match in TAG_PREF_RE.finditer(text):
            key = _decode_js_pref_string(match.group(1)).strip()
            label = _decode_js_pref_string(match.group(2)).strip()
            if not key or not label:
                continue
            tag_map[key] = label
            tag_map[key.lower()] = label
        if tag_map:
            return tag_map, errors

    errors.append(
        "Nem találtam Thunderbird tag névfeloldást a prefs.js-ben. "
        "A mailbox tag-kulcsokat így is beolvasom, de a címkenevekhez a `profile_root:` mutasson a Thunderbird profilkönyvtárra."
    )
    return tag_map, errors


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


def _split_thunderbird_tag_header(raw: str) -> list[str]:
    if not raw:
        return []
    # Thunderbird's X-Mozilla-Keys is whitespace-separated; other clients often
    # use comma-separated Keywords/X-Keywords. Split both, but keep quoted text
    # already decoded by email.header handling as plain text.
    return [bit.strip() for bit in re.split(r"[,\s]+", raw) if bit.strip()]


def _normalize_message_id(value: str) -> str:
    return value.strip().strip("<>").lower()


def thunderbird_mid_link(message_id: str) -> str:
    clean = str(message_id or "").strip().strip("<>").strip()
    return f"[E-mail link](mid:{clean})" if clean else ""


def thunderbird_mid_url(message_id: str) -> str:
    clean = str(message_id or "").strip().strip("<>").strip()
    return f"mid:{clean}" if clean else ""


def _decode_mork_value(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\\\n", "").replace("\\\r\n", "")

    def replace_hex(match: re.Match[str]) -> str:
        try:
            return bytes([int(match.group(1), 16)]).decode("latin-1")
        except Exception:
            return match.group(0)

    return re.sub(r"\$([0-9A-Fa-f]{2})", replace_hex, value).replace("\\)", ")").strip()


def _map_tag_key(tag_key: str, tag_map: dict[str, str] | None = None) -> str:
    clean = tag_key.strip()
    lowered = clean.lower()
    return (tag_map or {}).get(clean) or (tag_map or {}).get(lowered) or DEFAULT_THUNDERBIRD_LABELS.get(lowered) or clean


def _known_tag_tokens(tag_map: dict[str, str] | None = None) -> list[str]:
    tokens: list[str] = []
    for key, value in (tag_map or {}).items():
        tokens.extend([key, value])
    tokens.extend(DEFAULT_THUNDERBIRD_LABELS.keys())
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        clean = str(token).strip()
        lowered = clean.lower()
        if not clean or lowered in seen:
            continue
        seen.add(lowered)
        unique.append(clean)
    return unique


def _tag_keys_in_text(text: str, tag_map: dict[str, str] | None = None) -> list[str]:
    normalized_text = f" {text.lower()} "
    matches: list[str] = []
    for token in _known_tag_tokens(tag_map):
        clean = token.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if lowered.startswith("$label"):
            if lowered in normalized_text:
                matches.append(clean)
            continue
        if re.search(rf"(?<![\w-]){re.escape(lowered)}(?![\w-])", normalized_text):
            matches.append(clean)
    return _merge_tag_lists(matches)


def extract_thunderbird_tags(message: Message, tag_map: dict[str, str] | None = None) -> list[str]:
    raw_values: list[str] = []
    for header_name in TAG_HEADER_NAMES:
        raw = message.get(header_name, "").strip()
        if raw:
            raw_values.append(decode_header_value(raw))
    tags: list[str] = []
    seen: set[str] = set()
    ignored = {"nonjunk", "junk"}
    for raw in raw_values:
        for bit in _split_thunderbird_tag_header(raw):
            clean = bit.strip()
            if not clean:
                continue
            lowered = clean.lower()
            if lowered in ignored:
                continue
            display = _map_tag_key(clean, tag_map)
            if display in seen:
                continue
            seen.add(display)
            tags.append(display)
    return tags


def _merge_tag_lists(*tag_lists: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for tag_list in tag_lists:
        for tag in tag_list:
            clean = tag.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            merged.append(clean)
    return merged


def _parse_unfolded_headers(header_lines: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    current_name = ""
    current_value_parts: list[str] = []
    for line in header_lines:
        if line.startswith((" ", "\t")) and current_name:
            current_value_parts.append(line.strip())
            continue
        if current_name:
            headers[current_name] = " ".join(current_value_parts).strip()
        if ":" not in line:
            current_name = ""
            current_value_parts = []
            continue
        name, value = line.split(":", 1)
        current_name = name.lower().strip()
        current_value_parts = [value.strip()]
    if current_name:
        headers[current_name] = " ".join(current_value_parts).strip()
    return headers


def _tags_from_raw_header_values(headers: dict[str, str], tag_map: dict[str, str] | None = None) -> tuple[list[str], str]:
    tag_values: list[str] = []
    debug_parts: list[str] = []
    for header_name in TAG_HEADER_NAMES:
        raw = headers.get(header_name, "").strip()
        if not raw:
            continue
        decoded = decode_header_value(raw)
        debug_parts.append(f"{header_name}: {decoded}")
        for bit in _split_thunderbird_tag_header(decoded):
            lowered = bit.lower()
            if lowered in {"junk", "nonjunk"}:
                continue
            tag_values.append(_map_tag_key(bit, tag_map))
    return _merge_tag_lists(tag_values), " | ".join(debug_parts)


def _parse_message_key_candidates(value: str) -> list[int]:
    clean = str(value or "").strip()
    if not clean:
        return []
    candidates: list[int] = []
    for base in (10, 16):
        try:
            parsed = int(clean, base)
        except ValueError:
            continue
        if parsed not in candidates:
            candidates.append(parsed)
    return candidates


def load_mbox_message_id_offset_index(mailbox_path: Path) -> dict[int, str]:
    """Map raw mbox offsets and ordinal keys to RFC Message-ID values."""
    offset_index: dict[int, str] = {}
    try:
        handle = mailbox_path.open("rb")
    except Exception:
        return offset_index

    message_ordinal = -1

    def flush(offset: int | None, ordinal: int, header_lines: list[str]) -> None:
        if offset is None or not header_lines:
            return
        decoded_lines = [line.decode("latin-1", errors="replace") for line in header_lines]
        headers = _parse_unfolded_headers(decoded_lines)
        message_id = _normalize_message_id(headers.get("message-id", ""))
        if message_id:
            offset_index[offset] = message_id
            offset_index.setdefault(ordinal, message_id)

    with handle:
        current_offset: int | None = None
        header_lines: list[bytes] = []
        in_headers = False
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                flush(current_offset, message_ordinal, header_lines)
                break
            clean_line = line.rstrip(b"\r\n")
            if clean_line.startswith(b"From "):
                flush(current_offset, message_ordinal, header_lines)
                message_ordinal += 1
                current_offset = offset
                header_lines = []
                in_headers = True
                continue
            if not in_headers:
                continue
            if clean_line == b"":
                flush(current_offset, message_ordinal, header_lines)
                header_lines = []
                in_headers = False
                continue
            header_lines.append(clean_line)
    return offset_index


def _message_id_for_key(raw_message_key: str, offset_index: dict[int, str]) -> str:
    if not raw_message_key or not offset_index:
        return ""
    for message_key in _parse_message_key_candidates(raw_message_key):
        if message_key in offset_index:
            return offset_index[message_key]
        closest_offset = min(offset_index, key=lambda item: abs(item - message_key))
        if abs(closest_offset - message_key) <= 8:
            return offset_index[closest_offset]
    return ""


def load_raw_mbox_tag_index(mailbox_path: Path, tag_map: dict[str, str] | None = None) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Fast tag scan from raw mbox headers without parsing full message bodies."""
    tag_index: dict[str, list[str]] = {}
    debug_index: dict[str, str] = {}
    try:
        handle = mailbox_path.open("r", encoding="latin-1", errors="replace")
    except Exception:
        return tag_index, debug_index

    def flush_message(header_lines: list[str]) -> None:
        if not header_lines:
            return
        headers = _parse_unfolded_headers(header_lines)
        message_id = _normalize_message_id(headers.get("message-id", ""))
        if not message_id:
            return
        tags, debug = _tags_from_raw_header_values(headers, tag_map)
        if tags:
            tag_index[message_id] = _merge_tag_lists(tag_index.get(message_id, []), tags)
        if debug:
            debug_index[message_id] = debug

    with handle:
        header_lines: list[str] = []
        in_headers = False
        for line in handle:
            clean_line = line.rstrip("\r\n")
            if clean_line.startswith("From "):
                flush_message(header_lines)
                header_lines = []
                in_headers = True
                continue
            if not in_headers:
                continue
            if clean_line == "":
                flush_message(header_lines)
                header_lines = []
                in_headers = False
                continue
            header_lines.append(clean_line)
        flush_message(header_lines)
    return tag_index, debug_index


def load_msf_tag_index(mailbox_path: Path, tag_map: dict[str, str] | None = None) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Best-effort Thunderbird .msf tag reader keyed by Message-ID.

    IMAP/local Thunderbird folders may keep tag assignments in the companion
    .msf summary file instead of writing X-Mozilla-Keys back into the mbox.
    This parser intentionally stays lenient: it only needs message-id + keywords.
    """
    msf_path = mailbox_path.with_name(f"{mailbox_path.name}.msf")
    if not msf_path.exists():
        return {}, {}
    try:
        text = msf_path.read_text(encoding="latin-1", errors="replace")
    except Exception:
        return {}, {}

    atom_values = {match.group(1): _decode_mork_value(match.group(2)) for match in MSF_ATOM_RE.finditer(text)}
    column_ids = {key: value.lower() for key, value in atom_values.items()}
    message_id_columns = {
        key
        for key, value in column_ids.items()
        if value in {"message-id", "messageid"} or ("message" in value and "id" in value)
    }
    keyword_columns = {
        key
        for key, value in column_ids.items()
        if value in {"keywords", "x-mozilla-keys", "tags"} or "keyword" in value or "tag" in value
    }
    label_columns = {key for key, value in column_ids.items() if value == "label" or value.endswith(":label")}
    message_key_columns = {
        key
        for key, value in column_ids.items()
        if value in {"messagekey", "message-key", "key", "offset"} or ("message" in value and "key" in value)
    }
    tag_atom_ids = {
        atom_id
        for atom_id, atom_value in atom_values.items()
        if _tag_keys_in_text(atom_value, tag_map)
    }

    offset_index: dict[int, str] = {}
    tag_index: dict[str, list[str]] = {}
    debug_index: dict[str, str] = {}
    for row in re.findall(r"\[([^\]]+)\]", text, flags=re.DOTALL):
        row_message_key = row.split("(", 1)[0].strip()
        fields: dict[str, str] = {}
        referenced_atoms: set[str] = set()
        for match in MSF_FIELD_RE.finditer(row):
            column_id = match.group(1)
            direct_value = match.group(2)
            referenced_atom = match.group(3)
            if direct_value is not None:
                fields[column_id] = _decode_mork_value(direct_value)
            elif referenced_atom:
                referenced_atoms.add(referenced_atom)
                fields[column_id] = atom_values.get(referenced_atom, "")

        message_id = ""
        for column_id in message_id_columns:
            if fields.get(column_id):
                message_id = fields[column_id]
                break
        if not message_id:
            for value in fields.values():
                match = MESSAGE_ID_RE.search(value)
                if match:
                    message_id = match.group(0)
                    break
        if not message_id:
            match = MESSAGE_ID_RE.search(row)
            if match:
                message_id = match.group(0)
        if not message_id:
            if message_key_columns and not offset_index:
                offset_index = load_mbox_message_id_offset_index(mailbox_path)
            for column_id in message_key_columns:
                message_id = _message_id_for_key(fields.get(column_id, ""), offset_index)
                if message_id:
                    break
        if not message_id and row_message_key:
            if not offset_index:
                offset_index = load_mbox_message_id_offset_index(mailbox_path)
            message_id = _message_id_for_key(row_message_key, offset_index)
        if not message_id:
            continue

        raw_keywords: list[str] = []
        for column_id in keyword_columns:
            raw_value = fields.get(column_id, "")
            if raw_value:
                raw_keywords.extend(_split_thunderbird_tag_header(raw_value))
        for column_id in label_columns:
            raw_value = fields.get(column_id, "").strip()
            if raw_value and raw_value not in {"0", "none"}:
                raw_keywords.append(raw_value if raw_value.startswith("$label") else f"$label{raw_value}")
        for atom_id in referenced_atoms & tag_atom_ids:
            raw_keywords.extend(_tag_keys_in_text(atom_values.get(atom_id, ""), tag_map))
        if not raw_keywords:
            for raw_value in fields.values():
                raw_keywords.extend(_tag_keys_in_text(raw_value, tag_map))
        if not raw_keywords:
            continue

        tags = []
        for keyword in raw_keywords:
            lowered = keyword.lower()
            if lowered in {"junk", "nonjunk"}:
                continue
            tags.append(_map_tag_key(keyword, tag_map))
        normalized_message_id = _normalize_message_id(message_id)
        if normalized_message_id and tags:
            tag_index[normalized_message_id] = _merge_tag_lists(tag_index.get(normalized_message_id, []), tags)
            debug_index[normalized_message_id] = f"{msf_path.name}: " + ", ".join(raw_keywords)
    return tag_index, debug_index


def _first_existing_column(columns: list[str], candidates: list[str]) -> str:
    column_lookup = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in column_lookup:
            return column_lookup[candidate.lower()]
    return ""


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()]


def load_global_messages_tag_index(
    config: ThunderbirdImportConfig,
    tag_map: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], dict[str, str], list[str]]:
    """Read Thunderbird's global message DB for tag-like attributes.

    This is a best-effort bridge for profiles where tags are not written into
    the mbox headers. It keys results by RFC Message-ID so preview rows can join
    without exposing or importing full mail content.
    """
    errors: list[str] = []
    tag_index: dict[str, list[str]] = {}
    debug_index: dict[str, str] = {}
    db_paths = [root / "global-messages-db.sqlite" for root in _candidate_profile_roots(config)]
    seen_paths: set[str] = set()

    for db_path in db_paths:
        key = str(db_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        if not db_path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except Exception as exc:
            errors.append(f"Nem sikerült olvasni a global-messages-db.sqlite fájlt: {db_path} | {exc}")
            continue
        try:
            tables = _sqlite_tables(connection)
            if {"messages", "messageAttributes", "attributeDefinitions"}.issubset(tables):
                message_columns = _sqlite_columns(connection, "messages")
                attr_columns = _sqlite_columns(connection, "messageAttributes")
                attr_def_columns = _sqlite_columns(connection, "attributeDefinitions")

                message_pk = _first_existing_column(message_columns, ["id", "rowid"])
                header_message_id_col = _first_existing_column(message_columns, ["headerMessageID", "messageId", "messageID"])
                attr_message_id_col = _first_existing_column(attr_columns, ["messageID", "messageId"])
                attr_attribute_id_col = _first_existing_column(attr_columns, ["attributeID", "attributeId"])
                attr_value_col = _first_existing_column(attr_columns, ["value", "stringValue"])
                attr_def_pk = _first_existing_column(attr_def_columns, ["id", "attributeID", "attributeId"])
                attr_name_col = _first_existing_column(attr_def_columns, ["attributeName", "name"])
                if all([message_pk, header_message_id_col, attr_message_id_col, attr_attribute_id_col, attr_value_col, attr_def_pk, attr_name_col]):
                    query = f"""
                        SELECT m.{header_message_id_col}, ad.{attr_name_col}, ma.{attr_value_col}
                        FROM messageAttributes ma
                        JOIN messages m ON m.{message_pk} = ma.{attr_message_id_col}
                        JOIN attributeDefinitions ad ON ad.{attr_def_pk} = ma.{attr_attribute_id_col}
                        WHERE lower(ad.{attr_name_col}) LIKE '%tag%'
                           OR lower(ad.{attr_name_col}) LIKE '%keyword%'
                           OR lower(ad.{attr_name_col}) LIKE '%label%'
                    """
                    for message_id, attribute_name, raw_value in connection.execute(query):
                        normalized_message_id = _normalize_message_id(str(message_id or ""))
                        if not normalized_message_id:
                            continue
                        raw_text = str(raw_value or "").strip()
                        if not raw_text:
                            continue
                        raw_keywords = _split_thunderbird_tag_header(raw_text)
                        if not raw_keywords:
                            raw_keywords = [raw_text]
                        tags = [
                            _map_tag_key(keyword if not keyword.isdigit() else f"$label{keyword}", tag_map)
                            for keyword in raw_keywords
                            if keyword.lower() not in {"junk", "nonjunk"}
                        ]
                        if tags:
                            tag_index[normalized_message_id] = _merge_tag_lists(tag_index.get(normalized_message_id, []), tags)
                            debug_index[normalized_message_id] = "global DB: " + ", ".join(f"{attribute_name}={value}" for value in raw_keywords)
                else:
                    errors.append(f"A global DB klasszikus tag sémája nem ismert, széles keresésre váltok: {db_path}")

            # Fallback: schema-tolerant scan for known tag keys/names in text columns.
            tokens = _known_tag_tokens(tag_map)
            message_table_columns = _sqlite_columns(connection, "messages") if "messages" in tables else []
            message_pk_fallback = _first_existing_column(message_table_columns, ["id", "rowid"])
            header_message_id_fallback = _first_existing_column(message_table_columns, ["headerMessageID", "messageId", "messageID"])
            for table in sorted(tables):
                columns = _sqlite_columns(connection, table)
                if not columns:
                    continue
                direct_message_id_col = _first_existing_column(columns, ["headerMessageID", "messageId", "messageID"])
                message_fk_col = _first_existing_column(columns, ["messageID", "messageId"])
                text_columns = [column for column in columns if column not in {direct_message_id_col, message_fk_col}]
                for text_column in text_columns:
                    where_bits = []
                    params: list[str] = []
                    for token in tokens[:40]:
                        where_bits.append(f"{text_column} LIKE ?")
                        params.append(f"%{token}%")
                    if not where_bits:
                        continue
                    try:
                        if direct_message_id_col:
                            rows = connection.execute(
                                f"SELECT {direct_message_id_col}, {text_column} FROM {table} WHERE {' OR '.join(where_bits)} LIMIT 5000",
                                params,
                            ).fetchall()
                        elif message_fk_col and message_pk_fallback and header_message_id_fallback and table != "messages":
                            rows = connection.execute(
                                f"""
                                SELECT m.{header_message_id_fallback}, t.{text_column}
                                FROM {table} t
                                JOIN messages m ON m.{message_pk_fallback} = t.{message_fk_col}
                                WHERE {' OR '.join('t.' + bit for bit in where_bits)}
                                LIMIT 5000
                                """,
                                params,
                            ).fetchall()
                        else:
                            continue
                    except Exception:
                        continue
                    for message_id, raw_value in rows:
                        normalized_message_id = _normalize_message_id(str(message_id or ""))
                        if not normalized_message_id:
                            continue
                        raw_text = str(raw_value or "")
                        found_keys = _tag_keys_in_text(raw_text, tag_map)
                        tags = [_map_tag_key(key, tag_map) for key in found_keys]
                        if tags:
                            tag_index[normalized_message_id] = _merge_tag_lists(tag_index.get(normalized_message_id, []), tags)
                            debug_index[normalized_message_id] = f"global DB scan: {table}.{text_column}=" + ", ".join(found_keys)
        except Exception as exc:
            errors.append(f"Hiba a global-messages-db.sqlite tag olvasása közben: {db_path} | {exc}")
        finally:
            connection.close()

    return tag_index, debug_index, errors


def scan_thunderbird_tag_sources(
    config: ThunderbirdImportConfig,
    tag_map: dict[str, str] | None = None,
    max_hits: int = 200,
) -> tuple[list[dict], list[str]]:
    """Find where known Thunderbird tag keys/names occur in profile files.

    This intentionally returns only locations/counts/tokens, not mail bodies.
    """
    errors: list[str] = []
    hits: list[dict] = []
    tokens = _known_tag_tokens(tag_map)
    if not tokens:
        return hits, ["Nincs ismert Thunderbird tag-kulcs, amit keresni lehetne."]

    def add_hit(source: str, location: str, token: str, count: int) -> None:
        if count <= 0 or len(hits) >= max_hits:
            return
        hits.append({"source": source, "location": location, "token": token, "count": count})

    roots = _candidate_profile_roots(config)
    seen_files: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for msf_path in root.rglob("*.msf"):
            key = str(msf_path)
            if key in seen_files or len(hits) >= max_hits:
                continue
            seen_files.add(key)
            try:
                text = msf_path.read_text(encoding="latin-1", errors="replace")
            except Exception as exc:
                errors.append(f"Nem sikerült olvasni az msf fájlt: {msf_path} | {exc}")
                continue
            lowered = text.lower()
            for token in tokens:
                count = lowered.count(token.lower())
                if count:
                    add_hit("msf", str(msf_path), token, count)

    for db_path in [root / "global-messages-db.sqlite" for root in roots]:
        if not db_path.exists() or len(hits) >= max_hits:
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except Exception as exc:
            errors.append(f"Nem sikerült olvasni a global DB-t: {db_path} | {exc}")
            continue
        try:
            for table in sorted(_sqlite_tables(connection)):
                columns = _sqlite_columns(connection, table)
                for column in columns:
                    for token in tokens[:40]:
                        if len(hits) >= max_hits:
                            break
                        try:
                            row = connection.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?",
                                (f"%{token}%",),
                            ).fetchone()
                        except Exception:
                            continue
                        count = int(row[0] or 0) if row else 0
                        if count:
                            add_hit("global DB", f"{db_path} :: {table}.{column}", token, count)
        finally:
            connection.close()

    return hits, errors


def thunderbird_tag_header_debug(message: Message) -> str:
    parts: list[str] = []
    for header_name in TAG_HEADER_NAMES:
        raw = message.get(header_name, "").strip()
        if raw:
            parts.append(f"{header_name}: {decode_header_value(raw)}")
    return " | ".join(parts)


def has_attachment(message: Message) -> bool:
    if not message.is_multipart():
        disposition = (message.get("Content-Disposition") or "").lower()
        return "attachment" in disposition
    for part in message.walk():
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = (part.get_filename() or "").strip()
        if "attachment" in disposition or filename:
            return True
    return False


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


def preview_messages(
    inventory: list[ThunderbirdMailboxInventory],
    since_days: int | None,
    max_messages_per_mailbox: int,
    since_date: date | None = None,
    tag_map: dict[str, str] | None = None,
    global_tag_index: dict[str, list[str]] | None = None,
    global_tag_debug: dict[str, str] | None = None,
) -> tuple[list[ThunderbirdMessagePreview], list[str]]:
    previews: list[ThunderbirdMessagePreview] = []
    errors: list[str] = []
    min_dt: datetime | None = None
    if since_date is not None:
        min_dt = datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)
    elif since_days is not None:
        min_dt = datetime.now(timezone.utc) - timedelta(days=max(0, since_days))

    for mailbox_item in inventory:
        mailbox_path = Path(mailbox_item.path)
        try:
            mbox = mailbox.mbox(str(mailbox_path), create=False)
        except Exception as exc:
            errors.append(f"Nem sikerült megnyitni a mailboxot: {mailbox_path} | {exc}")
            continue

        msf_tag_index, msf_tag_debug = load_msf_tag_index(mailbox_path, tag_map)
        candidates: list[tuple[str, datetime | None, Message]] = []
        try:
            for message in mbox:
                sent_dt = _message_datetime(message)
                if min_dt is not None and sent_dt and sent_dt < min_dt:
                    continue
                candidates.append((sent_dt.isoformat() if sent_dt else "", sent_dt, message))

            candidates.sort(key=lambda item: item[0], reverse=True)
            collected: list[ThunderbirdMessagePreview] = []
            for sent_at, sent_dt, message in candidates[: max(1, max_messages_per_mailbox)]:
                body = extract_text_body(message)
                body_preview = re.sub(r"\s+", " ", body).strip()[:240]
                message_id = decoded_message_header(message, "message-id")
                normalized_message_id = _normalize_message_id(message_id)
                header_tags = extract_thunderbird_tags(message, tag_map)
                msf_tags = msf_tag_index.get(normalized_message_id, [])
                global_tags = (global_tag_index or {}).get(normalized_message_id, [])
                tag_debug = thunderbird_tag_header_debug(message)
                if msf_tag_debug.get(normalized_message_id):
                    tag_debug = " | ".join(part for part in [tag_debug, msf_tag_debug[normalized_message_id]] if part)
                if (global_tag_debug or {}).get(normalized_message_id):
                    tag_debug = " | ".join(part for part in [tag_debug, (global_tag_debug or {})[normalized_message_id]] if part)
                collected.append(
                    ThunderbirdMessagePreview(
                        preview_id=_preview_id(mailbox_path, message, sent_dt.isoformat() if sent_dt else ""),
                        mailbox_path=str(mailbox_path),
                        mailbox_name=mailbox_path.name,
                        account_hint=mailbox_item.account_hint,
                        subject=decoded_message_header(message, "subject") or "(tárgy nélkül)",
                        sender=decoded_message_header(message, "from"),
                        recipients=decoded_message_header(message, "to"),
                        cc=decoded_message_header(message, "cc"),
                        sent_at=sent_at,
                        message_id=message_id,
                        thunderbird_tags=", ".join(_merge_tag_lists(header_tags, msf_tags, global_tags)),
                        thunderbird_tag_headers=tag_debug,
                        has_attachment=has_attachment(message),
                        in_reply_to=decoded_message_header(message, "in-reply-to"),
                        references=decoded_message_header(message, "references"),
                        body_preview=body_preview,
                        body_full=body,
                    )
                )
        except Exception as exc:
            errors.append(f"Hiba üzenetbeolvasás közben: {mailbox_path} | {exc}")
            continue

        previews.extend(collected)

    previews.sort(key=lambda item: item.sent_at or "", reverse=True)
    return previews, errors


def thunderbird_preview_rows(previews: list[ThunderbirdMessagePreview]) -> list[dict]:
    return [
        {
            "selected": item.selected,
            "preview_id": item.preview_id,
            "from": item.sender,
            "to": item.recipients,
            "subject": item.subject,
            "thunderbird_tags": item.thunderbird_tags,
            "thunderbird_tag_headers": item.thunderbird_tag_headers,
            "cc": item.cc,
            "sent_at": item.sent_at,
            "mailbox": item.mailbox_name,
            "account": item.account_hint,
            "message_id": item.message_id,
            "email_mid_link": thunderbird_mid_link(item.message_id),
            "email_mid_url": thunderbird_mid_url(item.message_id),
            "has_attachment": item.has_attachment,
            "in_reply_to": item.in_reply_to,
            "references": item.references,
            "body_preview": item.body_preview,
        }
        for item in previews
    ]
