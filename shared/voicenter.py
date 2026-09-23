"""Voicenter CDR helpers: filter Sofia calls + turn aiData into our Transcript shape."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.secrets import env_value
from shared.stt import Transcript, TranscriptTurn


CDR_URL = "https://api.voicenter.com/hub/cdr/"

# Default: Sofia only (שיחות שיקוף / רגולציה על השלוחה שלה).
DEFAULT_EXTENSION = "LvMpqlBj"
DEFAULT_USER_ID = "211361"
DEFAULT_AGENT_NAME = "סופיה"

TOPIC_KEYWORDS = ("שיקוף", "רגולציה", "regulation", "mirror")


def classify_call_type(payload: dict[str, Any], transcript_text: str | None = None) -> str | None:
    hay = " ".join(
        [
            _norm(_pick(payload, "type", "Type", "queuename", "QueueName", "DepartmentName")),
            json.dumps(_pick(payload, "aiData", "AiData") or {}, ensure_ascii=False),
            transcript_text or "",
        ]
    ).lower()
    is_reg = any(k in hay for k in ("רגולציה", "regulation"))
    is_mirror = any(k in hay for k in ("שיקוף", "mirror"))
    if is_reg and not is_mirror:
        return "רגולציה"
    if is_mirror and not is_reg:
        return "שיקוף"
    if is_reg and is_mirror:
        return "שיקוף"
    return None


@dataclass
class VoicenterCall:
    call_id: str
    extension: str | None
    agent_name: str | None
    status: str | None
    call_type: str | None
    record_url: str | None
    duration_sec: float | None
    call_date: str | None
    caller: str | None
    did: str | None
    queue_name: str | None
    transcript: Transcript | None = None
    summary: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript and self.transcript.turns)


def _cfg(name: str, default: str = "") -> str:
    return (env_value(name) or os.environ.get(name) or default).strip()


def sofia_extension() -> str:
    return _cfg("VOICENTER_EXTENSION", DEFAULT_EXTENSION)


def sofia_user_id() -> str:
    return _cfg("VOICENTER_USER_ID", DEFAULT_USER_ID)


def sofia_agent_name() -> str:
    return _cfg("VOICENTER_AGENT_NAME", DEFAULT_AGENT_NAME)


def api_code() -> str:
    return _cfg("VOICENTER_API_CODE")


def require_answer() -> bool:
    return _cfg("VOICENTER_REQUIRE_ANSWER", "1") not in {"0", "false", "False", "no"}


def require_recording() -> bool:
    return _cfg("VOICENTER_REQUIRE_RECORDING", "0") not in {"0", "false", "False", "no"}


def topic_filter_enabled() -> bool:
    # Off by default until we see how Voicenter tags שיקוף/רגולציה.
    return _cfg("VOICENTER_TOPIC_FILTER", "0") in {"1", "true", "True", "yes"}


def inbox_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    raw = _cfg("VOICENTER_INBOX_DIR")
    path = Path(raw) if raw else (root / "inbox" / "voicenter")
    path.mkdir(parents=True, exist_ok=True)
    return path


def processed_dir() -> Path:
    path = inbox_dir() / "processed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _norm(s: Any) -> str:
    return str(s or "").strip()


def _pick(data: dict[str, Any], *keys: str) -> Any:
    lower = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def extension_matches(payload: dict[str, Any], extension: str | None = None) -> bool:
    ext = (extension or sofia_extension()).lower()
    user_id = sofia_user_id()
    agent = sofia_agent_name()
    candidates = [
        _pick(payload, "extenUser", "targetextension", "Targetextension", "callerextension", "Callerextension"),
        _pick(payload, "Extension", "extension", "TargetNumber"),
    ]
    for value in candidates:
        if ext and _norm(value).lower() == ext:
            return True
    blob = json.dumps(payload, ensure_ascii=False).lower()
    if ext and ext in blob:
        return True
    rep_code = _pick(payload, "representative_code", "RepresentativeCode", "UserId", "userId")
    if user_id and _norm(rep_code) == user_id:
        return True
    name = _norm(_pick(payload, "RepresentativeName", "UserName", "representative_name"))
    if agent and agent in name:
        return True
    if "סופיה" in name:
        return True
    return False


def is_answered(payload: dict[str, Any]) -> bool:
    status = _norm(_pick(payload, "status", "DialStatus", "dialStatus", "leg1DialStatusName")).upper()
    is_answer = _pick(payload, "isAnswer", "IsAnswer")
    if is_answer in (1, "1", True, "true", "True"):
        return True
    if status in {"ANSWER", "ANSWERED"}:
        return True
    duration = _pick(payload, "Duration", "duration", "actualCallDuration")
    try:
        if duration is not None and float(duration) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def has_recording(payload: dict[str, Any]) -> bool:
    url = _norm(_pick(payload, "record", "RecordURL", "recordUrl", "RecordUrl"))
    expect = _pick(payload, "RecordExpect", "recordExpect")
    return bool(url) or expect in (True, "true", "True", 1, "1")


def topic_matches(payload: dict[str, Any]) -> bool:
    if not topic_filter_enabled():
        return True
    hay = " ".join(
        [
            _norm(_pick(payload, "type", "Type", "queuename", "QueueName", "DepartmentName")),
            _norm(_pick(payload, "CustomData", "customData")),
            json.dumps(_pick(payload, "aiData", "AiData") or {}, ensure_ascii=False),
        ]
    ).lower()
    return any(k.lower() in hay for k in TOPIC_KEYWORDS)


def should_accept(payload: dict[str, Any]) -> tuple[bool, str]:
    if not extension_matches(payload):
        return False, "not_sofia_extension"
    if require_answer() and not is_answered(payload):
        return False, "not_answered"
    if require_recording() and not has_recording(payload):
        # Still accept if transcript exists without URL
        if not transcript_from_payload(payload):
            return False, "no_recording_or_transcript"
    if not topic_matches(payload):
        return False, "topic_filter"
    return True, "ok"


def transcript_from_payload(payload: dict[str, Any]) -> Transcript | None:
    ai = _pick(payload, "aiData", "AiData", "aidata")
    if isinstance(ai, str):
        try:
            ai = json.loads(ai)
        except json.JSONDecodeError:
            ai = None
    if not isinstance(ai, dict):
        return None
    rows = ai.get("transcript")
    if not isinstance(rows, list) or not rows:
        return None
    turns: list[TranscriptTurn] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = _norm(row.get("text"))
        if not text:
            continue
        speaker = _norm(row.get("speaker") or "Speaker0")
        # Map Speaker0/1 → agent/customer later in analysis; keep labels for now.
        start = row.get("startTime")
        end = row.get("endTime")
        try:
            start_f = float(start) if start is not None else None
        except (TypeError, ValueError):
            start_f = None
        try:
            end_f = float(end) if end is not None else None
        except (TypeError, ValueError):
            end_f = None
        turns.append(TranscriptTurn(speaker=speaker, text=text, start_sec=start_f, end_sec=end_f))
    if not turns:
        return None
    duration = None
    if turns[-1].end_sec is not None:
        duration = float(turns[-1].end_sec)
    return Transcript(language="he", turns=turns, provider="voicenter-ai", duration_sec=duration, raw=ai)


def summary_from_payload(payload: dict[str, Any]) -> str | None:
    ai = _pick(payload, "aiData", "AiData")
    if isinstance(ai, str):
        try:
            ai = json.loads(ai)
        except json.JSONDecodeError:
            return None
    if not isinstance(ai, dict):
        return None
    insights = ai.get("insights") if isinstance(ai.get("insights"), dict) else {}
    summary = insights.get("summary") or ai.get("summary")
    return _norm(summary) or None


def parse_cdr(payload: dict[str, Any]) -> VoicenterCall:
    call_id = _norm(
        _pick(payload, "ivruniqueid", "CallID", "callID", "callId", "ivrid", "UniqueIvrID")
    )
    duration_raw = _pick(payload, "actualCallDuration", "duration", "Duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    time_raw = _pick(payload, "time", "Date", "date")
    call_date = None
    if isinstance(time_raw, (int, float)):
        call_date = datetime.fromtimestamp(float(time_raw), tz=timezone.utc).isoformat()
    elif time_raw:
        call_date = _norm(time_raw)

    agent = _norm(
        _pick(
            payload,
            "representative_name",
            "RepresentativeName",
            "targetextension_name",
            "TargetextensionName",
            "callerextension_name",
            "CallerextensionName",
        )
    ) or sofia_agent_name()

    tr = transcript_from_payload(payload)
    classified = classify_call_type(payload, tr.as_text() if tr else None)

    return VoicenterCall(
        call_id=call_id or f"unknown-{datetime.now(timezone.utc).timestamp():.0f}",
        extension=_norm(
            _pick(payload, "extenUser", "targetextension", "Targetextension", "callerextension")
        )
        or None,
        agent_name=agent,
        status=_norm(_pick(payload, "status", "DialStatus")),
        call_type=classified or _norm(_pick(payload, "type", "Type")) or None,
        record_url=_norm(_pick(payload, "record", "RecordURL", "recordUrl")) or None,
        duration_sec=duration,
        call_date=call_date,
        caller=_norm(_pick(payload, "caller", "CallerNumber", "callerPhone")) or None,
        did=_norm(_pick(payload, "did", "DID")) or None,
        queue_name=_norm(_pick(payload, "queuename", "QueueName")) or None,
        transcript=tr,
        summary=summary_from_payload(payload),
        raw=payload,
    )


def save_inbox_payload(payload: dict[str, Any]) -> Path | None:
    ok, reason = should_accept(payload)
    call = parse_cdr(payload)
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", call.call_id)[:120] or "call"
    path = inbox_dir() / f"{safe_id}.json"
    envelope = {
        "accepted": ok,
        "reason": reason,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "call": {
            "call_id": call.call_id,
            "extension": call.extension,
            "agent_name": call.agent_name,
            "status": call.status,
            "call_type": call.call_type,
            "record_url": call.record_url,
            "duration_sec": call.duration_sec,
            "call_date": call.call_date,
            "has_transcript": call.has_transcript,
            "summary": call.summary,
        },
        "payload": payload,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path if ok else None


def list_pending_inbox() -> list[Path]:
    return sorted(inbox_dir().glob("*.json"))


def load_inbox_file(path: Path) -> tuple[VoicenterCall, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise ValueError(f"invalid voicenter inbox file: {path}")
    return parse_cdr(payload), data


def mark_processed(path: Path) -> Path:
    dest = processed_dir() / path.name
    if dest.exists():
        dest = processed_dir() / f"{path.stem}-{int(datetime.now().timestamp())}{path.suffix}"
    path.replace(dest)
    return dest


def status_path() -> Path:
    return inbox_dir().parent / "voicenter-status.json"


def write_runtime_status(payload: dict[str, Any]) -> None:
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_runtime_status() -> dict[str, Any]:
    path = status_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


_CDR_FIELDS = [
    "CallID",
    "Date",
    "Type",
    "CdrType",
    "DialStatus",
    "CallerNumber",
    "TargetNumber",
    "Targetextension",
    "Callerextension",
    "DID",
    "RecordURL",
    "RecordExpect",
    "Duration",
    "RepresentativeName",
    "RepresentativeCode",
    "UserName",
    "UserId",
    "QueueName",
    "aiData",
    "AiData",
]

_CALL_CACHE: dict[str, VoicenterCall] = {}


def find_saved_call(call_id: str) -> VoicenterCall | None:
    if not call_id:
        return None
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", call_id)[:120]
    for folder in (inbox_dir(), processed_dir()):
        for path in (folder / f"{safe}.json", *folder.glob(f"*{safe}*.json")):
            if not path.is_file():
                continue
            try:
                call, _ = load_inbox_file(path)
            except Exception:
                continue
            if call.call_id == call_id or path.stem.startswith(safe):
                return call
    return None


def _index_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        try:
            call = parse_cdr(row)
        except Exception:
            continue
        if call.call_id:
            _CALL_CACHE[call.call_id] = call


def _parse_when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_cdr_by_call_id(call_id: str, call_date: str | None = None) -> VoicenterCall | None:
    """One-call Call Log lookup — more likely to include aiData + a fresh RecordURL."""
    code = api_code()
    if not code or not call_id:
        return None
    center = _parse_when(call_date) or datetime.now(timezone.utc)
    start = center - timedelta(days=3)
    end = center + timedelta(hours=12)
    frm = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    to = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        data = _post_cdr(
            {
                "code": code,
                "search": {"fromdate": frm, "todate": to, "callID": call_id},
                "fields": _CDR_FIELDS,
            }
        )
    except Exception:
        return None
    rows = data.get("CDR_LIST") or data.get("calls") or []
    if not isinstance(rows, list):
        return None
    _index_rows([r for r in rows if isinstance(r, dict)])
    return _CALL_CACHE.get(call_id)


def download_record(url: str, dest: Path) -> Path:
    """Download one Voicenter PlayRecord URL.

    A single attempt. Repeating the same link with extra headers is what made
    Voicenter answer 429 and froze the queue on one call.
    """
    code = api_code()
    candidate = url
    if code and "code=" not in url:
        sep = "&" if "?" in url else "?"
        candidate = f"{url}{sep}code={code}"
    req = Request(
        candidate,
        headers={"User-Agent": "liba-call-qa/1.0", "Accept": "audio/*,*/*"},
    )
    try:
        with urlopen(req, timeout=120) as resp:
            payload = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("HTTP Error 429: Too Many Requests") from exc
        raise RuntimeError(f"PlayRecord HTTP {exc.code}") from exc
    head = payload[:200].lower()
    if len(payload) < 256 or b"<html" in head or "text/html" in ctype:
        raise RuntimeError("PlayRecord returned HTML instead of audio")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest


def refresh_call_by_id(call_id: str, call_date: str | None = None) -> VoicenterCall | None:
    """Re-pull Call Log so we get a fresh RecordURL / Voicenter transcript."""
    if call_id in _CALL_CACHE:
        return _CALL_CACHE[call_id]
    saved = find_saved_call(call_id)
    if saved and saved.has_transcript:
        _CALL_CACHE[call_id] = saved
        return saved
    # One Call Log lookup. Wide window pulls hit Voicenter's request cap
    # and were running before every recording download.
    return fetch_cdr_by_call_id(call_id, call_date)


def apply_call_to_recording(recording, call: VoicenterCall):
    """Return a Recording copy with fresh URL and Voicenter transcript when present."""
    from shared.sources import Recording

    text = recording.transcript_text
    segs = recording.transcript_segments
    provider = recording.transcript_provider
    if call.transcript and call.transcript.turns:
        text = call.transcript.as_text()
        segs = tuple(
            {
                "speaker": t.speaker,
                "text": t.text,
                "start_sec": t.start_sec,
                "end_sec": t.end_sec,
            }
            for t in call.transcript.turns
        )
        provider = call.transcript.provider
    return Recording(
        path=recording.path,
        source=recording.source,
        remote_id=recording.remote_id or call.call_id,
        name=recording.name,
        modified_time=call.call_date or recording.modified_time,
        size=recording.size,
        audio_url=call.record_url or recording.audio_url,
        agent_name=call.agent_name or recording.agent_name,
        duration_sec=call.duration_sec or recording.duration_sec,
        transcript_text=text,
        transcript_segments=segs,
        transcript_provider=provider,
    )


def hydrate_recording(recording):
    call_id = recording.remote_id
    if not call_id:
        return recording
    call = find_saved_call(call_id) or refresh_call_by_id(call_id, recording.modified_time)
    if not call:
        return recording
    return apply_call_to_recording(recording, call)


def _post_cdr(body: dict[str, Any]) -> dict[str, Any]:
    req = Request(
        CDR_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "liba-call-qa/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Voicenter Call Log HTTP {exc.code}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Voicenter Call Log unreachable: {exc.reason}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("unexpected Call Log response")
    if data.get("ERROR_NUMBER") not in (None, 0, "0"):
        raise RuntimeError(f"Call Log error: {data.get('ERROR_DESCRIPTION') or data}")
    return data


def fetch_cdr_pull(
    *,
    days: int = 7,
    extension: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    use_extension_filter: bool = True,
) -> list[dict[str, Any]]:
    """PULL Call Log. Often blocked unless server IP is allowlisted in Cpanel."""
    code = api_code()
    if not code:
        raise RuntimeError("VOICENTER_API_CODE is missing")
    ext = extension or sofia_extension()
    now = datetime.now(timezone.utc)
    to_dt = end or now
    from_dt = start or (to_dt - timedelta(days=days))
    frm = from_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    to = to_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    attempts: list[dict[str, Any]] = []
    search_with_ext: dict[str, Any] = {"fromdate": frm, "todate": to}
    search_plain: dict[str, Any] = {"fromdate": frm, "todate": to}
    if ext:
        search_with_ext["extensions"] = [ext]
    if use_extension_filter and ext:
        attempts.append({"search": search_with_ext, "fields": _CDR_FIELDS, "sort": [{"field": "Date", "order": "desc"}]})
    attempts.append({"search": search_plain, "fields": _CDR_FIELDS, "sort": [{"field": "Date", "order": "desc"}]})
    attempts.append({"search": search_plain})

    last_error: Exception | None = None
    data: dict[str, Any] | None = None
    for extra in attempts:
        try:
            data = _post_cdr({"code": code, **extra})
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error is not None or data is None:
        raise last_error or RuntimeError("Voicenter Call Log failed")
    rows = data.get("CDR_LIST") or data.get("calls") or []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]
