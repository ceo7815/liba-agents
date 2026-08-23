"""Drive worker: poll OS (or --once), skip already-done Drive files, transcribe + score."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.agent_config import load_call_qa_config
from shared.logging import log
from shared.os_client import MockOsClient, OsError, get_os_client
from shared.pipeline import process_recording
from shared.sources import get_recording_source
from shared.stt import get_stt_provider


def main() -> int:
    parser = argparse.ArgumentParser(description="call-qa Drive worker")
    parser.add_argument("--once", action="store_true", help="Process Drive now; do not wait for OS button")
    parser.add_argument("--watch", action="store_true", help="Poll os.poll_work until interrupted")
    parser.add_argument("--mock-queue", action="store_true", help="Simulate OS button in mock mode, then process once")
    parser.add_argument("--interval", type=int, default=15, help="Seconds between polls in --watch")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if OS already marked the call done")
    parser.add_argument("--only", help="Process only recordings whose Drive id or file name contains this")
    args = parser.parse_args()

    cfg = load_call_qa_config()
    os_cfg = cfg["os"]
    source_cfg = cfg["source"]
    stt_cfg = cfg["stt"]
    slug = cfg["agent"].get("os_slug") or "call-control"

    client = get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=os_cfg.get("base_url") or os.environ.get("LIBA_OS_BASE_URL"),
        api_key=os.environ.get("LIBA_OS_API_KEY"),
        agent_slug=slug,
    )
    source = get_recording_source(
        source_cfg.get("type") or "drive",
        local_dir=source_cfg.get("_local_dir"),
        drive_folder_id=(source_cfg.get("drive") or {}).get("folder_id"),
        cache_dir=source_cfg.get("_cache_dir"),
    )
    stt = get_stt_provider(stt_cfg.get("provider"))
    report_agent_tools(client, source_cfg)

    if args.mock_queue:
        if not isinstance(client, MockOsClient):
            print("--mock-queue only works in os.mode: mock", file=sys.stderr)
            return 2
        queued = client.queue_work()
        print(f"Mock queued run {queued}")
        return run_batch(client, source, stt, run_id=queued, language=stt_cfg.get("language") or "auto", force=args.force, only=args.only)

    if args.watch:
        print(f"Watching OS poll_work every {args.interval}s (Ctrl+C to stop)")
        try:
            return watch_loop(client, source, stt, args.interval, stt_cfg.get("language") or "auto", force=args.force)
        except KeyboardInterrupt:
            print("Stopped.")
            return 0

    if not args.once and not args.watch and not args.mock_queue:
        print("Use --once (process Drive now) or --watch (wait for OS button).", file=sys.stderr)
        return 2

    return run_batch(client, source, stt, run_id=None, language=stt_cfg.get("language") or "auto", force=args.force, only=args.only)


def report_agent_tools(client, source_cfg: dict) -> None:
    """Fill Liba OS Connections tab. OS itself has no Drive/OpenAI login."""
    from shared.drive_hermes import is_authenticated
    from shared.secrets import env_value

    drive_ok = is_authenticated()
    openai_ok = bool(env_value("OPENAI_API_KEY"))
    folder_id = (source_cfg.get("drive") or {}).get("folder_id")
    tools = [
        (
            "google-drive",
            "source",
            "connected" if drive_ok else "disconnected",
            {"folder_id": folder_id} if folder_id else None,
        ),
        (
            "openai-stt",
            "stt",
            "connected" if openai_ok else "disconnected",
            {"model": "gpt-4o-transcribe-diarize"},
        ),
        (
            "openai-gpt-5.4-mini",
            "llm",
            "connected" if openai_ok else "disconnected",
            {"model": "gpt-5.4-mini"},
        ),
    ]
    for name, tool_type, status, metadata in tools:
        try:
            client.report_tool_status(name, tool_type, status, metadata=metadata)
            print(f"tool {name}: {status}")
        except OsError as exc:
            print(f"tool {name}: report failed: {exc}")


def watch_loop(client, source, stt, interval: int, language: str, force: bool = False) -> int:
    try:
        client.heartbeat("online")
    except Exception as exc:
        log("heartbeat_error", error=str(exc))
        print(f"heartbeat failed: {exc}")
    try:
        while True:
            try:
                work = client.poll_work()
            except OsError as exc:
                log("poll_error", error=str(exc))
                print(f"poll_work failed: {exc}")
                time.sleep(interval)
                continue
            except Exception as exc:
                log("poll_error", error=str(exc))
                print(f"poll_work failed: {exc}")
                time.sleep(interval)
                continue
            if work.get("has_work"):
                run_batch(
                    client,
                    source,
                    stt,
                    run_id=work.get("run_id"),
                    language=language,
                    force=force,
                )
            else:
                try:
                    client.heartbeat("online")
                except Exception:
                    pass
            time.sleep(interval)
    finally:
        try:
            client.heartbeat("offline")
        except Exception:
            pass



def run_batch(client, source, stt, run_id: str | None, language: str, force: bool = False, only: str | None = None) -> int:
    started = client.start_run("manual", metadata={"source": "drive"}, run_id=run_id)
    run_id = str(started["run_id"])
    log("run_start", run_id=run_id)
    processed = failed = skipped = 0
    recordings = source.list_new()
    if only:
        needle = only.lower()
        recordings = [
            rec
            for rec in recordings
            if needle in (rec.remote_id or "").lower() or needle in (rec.name or "").lower()
        ]
        print(f"Filtered to {len(recordings)} file(s) matching {only!r}")
    print(f"Audio files found: {len(recordings)}")
    if not recordings:
        client.log(run_id, "info", "Drive folder has no audio files")
        client.finish_run(run_id, "success", items_processed=0, items_failed=0)
        print("Nothing to process. Upload audio to the Drive folder, then run again.")
        return 0

    for rec in recordings:
        result = process_recording(client, source, stt, rec, run_id, language=language, force=force)
        if result == "ok":
            processed += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1
        print(f"  {result}: {rec.name or rec.remote_id}")

    status = "success" if failed == 0 else ("partial" if processed else "failed")
    client.finish_run(run_id, status, items_processed=processed, items_failed=failed)
    print(f"Run {run_id}: ok={processed} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
