"""Identify a call from the Drive file name and Drive timestamps."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Voice Center / Link style: ..._user_1806_1806-13082026172003-1786630802.mp3
_STAMP = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})(?!\d)")
_USER = re.compile(r"user[_-](\d+)", re.I)


def drive_file_url(file_id: str | None) -> str | None:
    if not file_id:
        return None
    return f"https://drive.google.com/file/d/{file_id}/view"


def parse_call_datetime(name: str | None) -> str | None:
    """Return ISO datetime if the file name contains DDMMYYYYHHMMSS."""
    if not name:
        return None
    match = _STAMP.search(name)
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    try:
        tz = ZoneInfo("Asia/Jerusalem")
    except Exception:
        tz = timezone(timedelta(hours=3))
    try:
        dt = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=tz
        )
    except ValueError:
        return None
    return dt.isoformat()


def parse_user_id(name: str | None) -> str | None:
    if not name:
        return None
    match = _USER.search(name)
    return match.group(1) if match else None


def display_name(
    name: str | None,
    call_date_iso: str | None = None,
    customer_name: str | None = None,
) -> str:
    customer_name = clean_person_name(customer_name)
    date_label = None
    if call_date_iso:
        try:
            dt = datetime.fromisoformat(call_date_iso)
            date_label = dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            date_label = call_date_iso
    if customer_name and date_label:
        return f"{customer_name} · {date_label}"
    if customer_name:
        return customer_name
    if date_label:
        user = parse_user_id(name)
        suffix = f" (שלוחה {user})" if user else ""
        return f"לקוח לא זוהה · {date_label}{suffix}"
    return name or "הקלטה ללא שם"


def format_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


_HEB_NAME = re.compile(r"[א-ת]{2,15}(?:[ \-][א-ת]{2,15})?")
_LATIN_CYR_NAME = re.compile(
    r"[A-Za-zА-Яа-яЁё]{2,20}(?:[ \-][A-Za-zА-Яа-яЁё]{2,20})?"
)
_SKIP_NAMES = {
    "כן",
    "לא",
    "רק",
    "מה",
    "טוב",
    "יופי",
    "אוקיי",
    "אוקי",
    "בסדר",
    "רגע",
    "שלום",
    "תקין",
    "חלקי",
    "כשל",
    "לשיפור",
    "קריטי",
    "מהותי",
    "לקוח",
    "סוכן",
    "נציג",
    "גברת",
    "אדון",
    "כבוד",
    "unknown",
    "null",
    "none",
    "ליבה",
    "הראל",
    "מגדל",
    "מנורה",
    "כלל",
    "הפניקס",
    "הכשרה",
    "איילון",
    "ביטוח",
    "פוליסה",
    "מכירה",
    "שירות",
    "הקבוע",
    "הכול",
    "הבדיקה",
    "השיחה",
}

_NAME_PATTERNS = (
    re.compile(r"שלום[,\s]+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"שמי\s+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"השם שלי\s+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"קוראים לי\s+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"מדבר(?:ת)?\s+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"זה\s+(" + _HEB_NAME.pattern + r")(?:\s|$|[.,!?])"),
    re.compile(r"שלך\s+(" + _HEB_NAME.pattern + r")"),
    re.compile(r"(?i)меня зовут\s+(" + _LATIN_CYR_NAME.pattern + r")"),
    re.compile(r"(?i)my name is\s+(" + _LATIN_CYR_NAME.pattern + r")"),
)


def is_person_name(value: str | None) -> bool:
    name = clean_person_name(value)
    return name is not None


def clean_person_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip().strip(".,;:!?\"'“”")
    if not name or name.lower() in _SKIP_NAMES or name in _SKIP_NAMES:
        return None
    if name.startswith("שיחה ") or name.startswith("לקוח לא"):
        return None
    if not (_HEB_NAME.fullmatch(name) or _LATIN_CYR_NAME.fullmatch(name)):
        return None
    return name


def guess_names_from_transcript(text: str) -> tuple[str | None, str | None]:
    """Best-effort customer/agent names from greetings. Never invent unseen names."""
    found: list[str] = []
    blob = text or ""
    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(blob):
            name = clean_person_name(match.group(1))
            if not name or name in found:
                continue
            found.append(name)
            if len(found) == 2:
                break
        if len(found) == 2:
            break
    customer = found[0] if found else None
    agent = found[1] if len(found) > 1 else None
    return customer, agent
