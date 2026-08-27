"""Run call-qa on one local audio file (manual test, same pipeline as the worker)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.agent_config import load_call_qa_config
from shared.logging import log
from shared.os_client import get_os_client
from shared.pipeline import process_recording
from shared.sources import LocalFolderSource, Recording
from shared.stt import get_stt_provider


def main() -> int:
    parser = argparse.ArgumentParser(description="Run call-qa on one local audio file (manual).")
    parser.add_argument("audio", type=Path, help="Path to wav/mp3/m4a")
    args = parser.parse_args()
    audio = args.audio.expanduser().resolve()

    if not audio.is_file():
        log("error", reason="audio_not_found", path=str(audio))
        print(f"File not found: {audio}", file=sys.stderr)
        return 2

    cfg = load_call_qa_config()
    os_cfg = cfg["os"]
    stt_cfg = cfg["stt"]
    slug = cfg["agent"].get("os_slug") or "call-control"

    client = get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=os.environ.get("LIBA_OS_BASE_URL") or os_cfg.get("base_url"),
        api_key=os.environ.get("LIBA_OS_API_KEY"),
        agent_slug=slug,
    )
    stt = get_stt_provider(stt_cfg.get("provider"))
    source = LocalFolderSource(audio.parent)
    recording = Recording(path=audio, source="local", remote_id=audio.name, name=audio.name)

    started = client.start_run("manual", metadata={"source": "local", "file": audio.name})
    run_id = str(started["run_id"])
    result = process_recording(
        client,
        source,
        stt,
        recording,
        run_id,
        language=stt_cfg.get("language") or "auto",
    )
    status = "success" if result == "ok" else ("success" if result == "skipped" else "failed")
    client.finish_run(
        run_id,
        status,
        items_processed=1 if result == "ok" else 0,
        items_failed=1 if result == "failed" else 0,
    )
    print(f"{result}: {audio.name}  run={run_id}")
    return 0 if result != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
