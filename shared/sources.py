"""Where recordings come from.

call-qa never talks to Drive or Voice Center ad-hoc.
Swap the source; the agent still receives a local audio path.
Drive uses Hermes google_token.json (google-workspace skill).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Recording:
    """One audio file ready for STT + QA (or pre-made transcript from Voicenter)."""

    path: Path
    source: str
    remote_id: str | None = None
    name: str | None = None
    modified_time: str | None = None
    size: int | None = None
    audio_url: str | None = None
    agent_name: str | None = None
    duration_sec: float | None = None
    transcript_text: str | None = None
    transcript_segments: tuple[dict, ...] | None = None
    transcript_provider: str | None = None


class RecordingSource(ABC):
    @abstractmethod
    def list_new(self) -> list[Recording]:
        """Recordings not yet processed (or not yet cached locally)."""

    @abstractmethod
    def fetch(self, recording: Recording) -> Path:
        """Ensure the file is on disk and return a local path."""


class LocalFolderSource(RecordingSource):
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def list_new(self) -> list[Recording]:
        files = sorted(
            p
            for p in self.folder.iterdir()
            if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4"}
        )
        return [Recording(path=p, source="local", remote_id=p.name, name=p.name) for p in files]

    def fetch(self, recording: Recording) -> Path:
        if not recording.path.exists():
            raise FileNotFoundError(recording.path)
        return recording.path


class DriveSource(RecordingSource):
    """Shared Google Drive folder. Auth = Hermes google-workspace token."""

    def __init__(self, folder_id: str, cache_dir: Path) -> None:
        if not folder_id:
            raise ValueError("Drive folder_id is required")
        self.folder_id = folder_id
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def list_new(self) -> list[Recording]:
        from shared.drive_hermes import is_audio_file, list_folder_files

        out: list[Recording] = []
        for item in list_folder_files(self.folder_id):
            name = item.get("name") or item["id"]
            if not is_audio_file(name, item.get("mimeType")):
                continue
            dest = self.cache_dir / f"{item['id']}_{name}"
            out.append(
                Recording(
                    path=dest,
                    source="drive",
                    remote_id=item["id"],
                    name=name,
                    modified_time=item.get("modifiedTime") or item.get("createdTime"),
                    size=int(item["size"]) if item.get("size") else None,
                )
            )
        return out

    def fetch(self, recording: Recording) -> Path:
        from shared.drive_hermes import download_file

        if recording.path.exists() and recording.path.stat().st_size > 0:
            return recording.path
        if not recording.remote_id:
            raise FileNotFoundError("Drive recording missing remote_id")
        return download_file(recording.remote_id, recording.path)


class OsPendingSource(RecordingSource):
    """Calls claimed via Liba OS `calls.get_pending` (direct uploads)."""

    def __init__(self, client, cache_dir: Path, calls: list[dict] | None = None) -> None:
        self.client = client
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._calls = calls

    def list_new(self) -> list[Recording]:
        calls = self._calls
        if calls is None:
            payload = self.client.get_pending(limit=20)
            calls = list(payload.get("calls") or [])
            self._calls = calls

        out: list[Recording] = []
        for row in calls:
            external_id = str(row.get("external_id") or row.get("id") or "")
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            name = (
                (meta.get("file_name") if isinstance(meta.get("file_name"), str) else None)
                or (meta.get("display_name") if isinstance(meta.get("display_name"), str) else None)
                or external_id
                or "upload.bin"
            )
            url = row.get("download_url") or row.get("audio_path")
            url = url if isinstance(url, str) and url.startswith("http") else None
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:120]
            dest = self.cache_dir / f"{external_id.replace(':', '_')}_{safe}"
            out.append(
                Recording(
                    path=dest,
                    source="voicenter" if str(row.get("source") or "") == "voicenter" else "upload",
                    remote_id=external_id,
                    name=name,
                    modified_time=row.get("call_date") if isinstance(row.get("call_date"), str) else None,
                    audio_url=url,
                    agent_name=meta.get("agent_name") if isinstance(meta.get("agent_name"), str) else None,
                    duration_sec=float(row["duration_sec"]) if row.get("duration_sec") is not None else None,
                )
            )
        return out

    def fetch(self, recording: Recording) -> Path:
        if recording.path.exists() and recording.path.stat().st_size > 0:
            return recording.path
        url = recording.audio_url
        if not url:
            raise FileNotFoundError(f"Upload recording missing URL: {recording.remote_id}")
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "liba-call-qa/1.0"})
        with urlopen(req, timeout=120) as resp:
            data = resp.read()
        recording.path.parent.mkdir(parents=True, exist_ok=True)
        recording.path.write_bytes(data)
        return recording.path


class VoiceCenterSource(RecordingSource):
    """Pending Voicenter CDR JSON files from PUSH webhook inbox."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        from shared.voicenter import inbox_dir

        self.cache_dir = cache_dir or (Path(__file__).resolve().parents[1] / "inbox" / "voicenter-audio")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._inbox = inbox_dir()

    def list_new(self) -> list[Recording]:
        from shared.voicenter import list_pending_inbox, load_inbox_file

        out: list[Recording] = []
        for path in list_pending_inbox():
            try:
                call, envelope = load_inbox_file(path)
            except Exception:
                continue
            if envelope.get("accepted") is False:
                continue
            segments = None
            text = None
            provider = None
            if call.transcript:
                text = call.transcript.as_text()
                provider = call.transcript.provider
                segments = tuple(
                    {
                        "speaker": t.speaker,
                        "text": t.text,
                        "start_sec": t.start_sec,
                        "end_sec": t.end_sec,
                    }
                    for t in call.transcript.turns
                )
            stub = self.cache_dir / f"{call.call_id}.mp3"
            out.append(
                Recording(
                    path=stub,
                    source="voicenter",
                    remote_id=call.call_id,
                    name=f"sofia-{call.call_id}.mp3",
                    modified_time=call.call_date,
                    audio_url=call.record_url,
                    agent_name=call.agent_name,
                    duration_sec=call.duration_sec,
                    transcript_text=text,
                    transcript_segments=segments,
                    transcript_provider=provider,
                )
            )
        return out

    def fetch(self, recording: Recording) -> Path:
        if recording.path.exists() and recording.path.stat().st_size > 0:
            return recording.path
        url = recording.audio_url
        if not url:
            # Transcript-only path: create empty marker so callers have a Path.
            recording.path.parent.mkdir(parents=True, exist_ok=True)
            if not recording.path.exists():
                recording.path.write_bytes(b"")
            return recording.path
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "liba-call-qa/1.0"})
        with urlopen(req, timeout=120) as resp:
            data = resp.read()
        recording.path.parent.mkdir(parents=True, exist_ok=True)
        recording.path.write_bytes(data)
        return recording.path


def get_recording_source(
    kind: str,
    local_dir: Path | None = None,
    drive_folder_id: str | None = None,
    cache_dir: Path | None = None,
    os_client=None,
    pending_calls: list[dict] | None = None,
) -> RecordingSource:
    root = Path(__file__).resolve().parents[1]
    if kind == "local":
        return LocalFolderSource(local_dir or (root / "inbox"))
    if kind == "drive":
        return DriveSource(
            folder_id=drive_folder_id or "",
            cache_dir=cache_dir or (root / "inbox" / "drive-cache"),
        )
    if kind in {"os_pending", "upload", "pending_calls"}:
        if os_client is None:
            raise ValueError("os_client is required for OS pending source")
        return OsPendingSource(
            os_client,
            cache_dir=cache_dir or (root / "inbox" / "upload-cache"),
            calls=pending_calls,
        )
    if kind in {"voice_center", "voicecenter", "voicenter", "voicenter_push"}:
        return VoiceCenterSource(cache_dir=cache_dir or (root / "inbox" / "voicenter-audio"))
    raise ValueError(f"Unknown recording source: {kind!r}")
