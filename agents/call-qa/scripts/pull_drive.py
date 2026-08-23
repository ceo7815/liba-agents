"""List/download new audio from the configured Google Drive folder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.drive_hermes import auth_status, is_authenticated
from shared.logging import log
from shared.sources import get_recording_source

FOLDER_ID = "1PQYaDHFbJDqBWnBn0B4cbnVYPOdCo9Fg"


def main() -> int:
    status = auth_status()
    print(f"Drive auth: {status['authenticated']}  ({status['token_path']})")
    if not is_authenticated():
        print()
        print("Not connected yet. Complete Google OAuth (Hermes google-workspace), then re-run.")
        return 2

    source = get_recording_source("drive", drive_folder_id=FOLDER_ID)
    items = source.list_new()
    print(f"Audio files in Drive folder: {len(items)}")
    if not items:
        print("Folder is empty of audio, or this Google account cannot see the files.")
        print("Share the folder with the same Google account you authorize.")
        return 0

    for rec in items:
        log("drive_fetch", id=rec.remote_id, name=rec.name)
        path = source.fetch(rec)
        print(f"  {rec.name} → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
