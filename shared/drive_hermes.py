"""Google Drive via Hermes OAuth token (google_token.json).

Does not invent a second login. Auth is `google-workspace` setup.py.
Listing uses supportsAllDrives so a shared folder works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".mp4", ".wma"}
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return Path(raw)
    local = Path.home() / "AppData" / "Local" / "hermes"
    if local.exists():
        return local
    return Path.home() / ".hermes"


def token_path() -> Path:
    return hermes_home() / "google_token.json"


def is_authenticated() -> bool:
    return token_path().exists()


def _service():
    if not is_authenticated():
        raise RuntimeError(
            "Google Drive is not connected. Run Hermes google-workspace setup "
            f"(missing {token_path()})."
        )
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path()), [DRIVE_SCOPE])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder_files(folder_id: str, max_files: int = 200) -> list[dict]:
    service = _service()
    query = f"'{folder_id}' in parents and trashed = false"
    files: list[dict] = []
    page_token = None
    while len(files) < max_files:
        resp = (
            service.files()
            .list(
                q=query,
                pageSize=min(100, max_files - len(files)),
                pageToken=page_token,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, createdTime, size)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def is_audio_file(name: str, mime: str | None = None) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return True
    mime = (mime or "").lower()
    return mime.startswith("audio/") or mime in {"video/mp4", "video/quicktime"}


def download_file(file_id: str, dest: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload

    service = _service()
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with dest.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest


def auth_status() -> dict:
    path = token_path()
    payload = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "authenticated": path.exists(),
        "token_path": str(path),
        "has_refresh_token": bool(payload.get("refresh_token")),
    }
