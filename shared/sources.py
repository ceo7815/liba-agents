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
    """One audio file ready for STT + QA."""

    path: Path
    source: str
    remote_id: str | None = None
    name: str | None = None
    modified_time: str | None = None
    size: int | None = None


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
            if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
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


class VoiceCenterSource(RecordingSource):
    def list_new(self) -> list[Recording]:
        raise NotImplementedError("Voice Center API is not wired yet.")

    def fetch(self, recording: Recording) -> Path:
        raise NotImplementedError("Voice Center API is not wired yet.")


def get_recording_source(
    kind: str,
    local_dir: Path | None = None,
    drive_folder_id: str | None = None,
    cache_dir: Path | None = None,
) -> RecordingSource:
    root = Path(__file__).resolve().parents[1]
    if kind == "local":
        return LocalFolderSource(local_dir or (root / "inbox"))
    if kind == "drive":
        return DriveSource(
            folder_id=drive_folder_id or "",
            cache_dir=cache_dir or (root / "inbox" / "drive-cache"),
        )
    if kind in {"voice_center", "voicecenter"}:
        return VoiceCenterSource()
    raise ValueError(f"Unknown recording source: {kind!r}")
