"""call-qa worker: poll Liba OS for uploaded recordings, transcribe + score.

Drive ingest is disabled for now — only OS direct uploads via calls.get_pending.
"""

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
    parser = argparse.ArgumentParser(description="call-qa upload worker")
    parser.add_argument("--once", action="store_true", help="Claim pending OS uploads once and process")
    parser.add_argument("--watch", action="store_true", help="Poll os.poll_work until interrupted")
    parser.add_argument("--mock-queue", action="store_true", help="Simulate OS button in mock mode, then process once")
    parser.add_argument("--interval", type=int, default=10, help="Seconds between polls in --watch")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if OS already marked the call done")
    parser.add_argument("--only", help="Process only recordings whose id or file name contains this")
    args = parser.parse_args()

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
    report_agent_tools(client)

    if args.mock_queue:
        if not isinstance(client, MockOsClient):
            print("--mock-queue only works in os.mode: mock", file=sys.stderr)
            return 2
        queued = client.queue_work()
        print(f"Mock queued run {queued}")
        return run_upload_batch(
            client,
            stt,
            run_id=queued,
            language=stt_cfg.get("language") or "auto",
            force=args.force,
            only=args.only,
        )

    if args.watch:
        print(f"Watching OS poll_work every {args.interval}s for uploads (Ctrl+C to stop)")
        try:
            return watch_loop(client, stt, args.interval, stt_cfg.get("language") or "auto", force=args.force)
        except KeyboardInterrupt:
            print("Stopped.")
            return 0

    if not args.once and not args.watch and not args.mock_queue:
        print("Use --once (process pending uploads) or --watch (wait for OS button).", file=sys.stderr)
        return 2

    return run_upload_batch(
        client,
        stt,
        run_id=None,
        language=stt_cfg.get("language") or "auto",
        force=args.force,
        only=args.only,
    )


def report_agent_tools(client) -> None:
    from shared.secrets import env_value

    openai_ok = bool(env_value("OPENAI_API_KEY"))
    tools = [
        ("os-upload", "source", "connected", {"mode": "pending_calls"}),
        (
            "openai-stt",
            "stt",
            "connected" if openai_ok else "disconnected",
            {"model": "gpt-4o-transcribe-diarize"},
        ),
        (
            "openai-gpt-5.6-sol",
            "llm",
            "connected" if openai_ok else "disconnected",
            {"model": "gpt-5.6-sol"},
        ),
    ]
    for name, tool_type, status, metadata in tools:
        try:
            client.report_tool_status(name, tool_type, status, metadata=metadata)
            print(f"tool {name}: {status}")
        except OsError as exc:
            print(f"tool {name}: report failed: {exc}")


def watch_loop(client, stt, interval: int, language: str, force: bool = False) -> int:
    try:
        client.heartbeat("online")
    except Exception as exc:
        log("heartbeat_error", error=str(exc))
        print(f"heartbeat failed: {exc}")
    try:
        while True:
            run_id = None
            try:
                work = client.poll_work()
            except Exception as exc:
                log("poll_error", error=str(exc))
                print(f"poll_work failed: {exc}")
                work = {"has_work": False}

            if work.get("has_work"):
                meta = work.get("metadata") if isinstance(work.get("metadata"), dict) else {}
                ingest = str(meta.get("ingest") or meta.get("source") or "pending_calls")
                run_id = work.get("run_id")
                if ingest in {"drive"}:
                    print(f"Skipping Drive job {run_id} — upload-only mode")
                    try:
                        client.start_run("manual", metadata={"source": "upload"}, run_id=run_id)
                        client.finish_run(
                            str(run_id),
                            "cancelled",
                            items_processed=0,
                            items_failed=0,
                            error_message="Drive ingest disabled; use OS upload",
                        )
                    except Exception as exc:
                        print(f"cancel drive job failed: {exc}")
                    run_id = None

            # Always drain pending uploads so analysis starts immediately.
            try:
                pending = client.get_pending(limit=20)
                calls = list(pending.get("calls") or [])
            except Exception as exc:
                log("pending_drain_error", error=str(exc))
                print(f"get_pending failed: {exc}")
                calls = []

            if calls:
                print(f"Processing {len(calls)} pending upload(s)")
                run_upload_batch(
                    client,
                    stt,
                    run_id=run_id,
                    language=language,
                    force=force,
                    pending_calls=calls,
                )
            elif run_id:
                # Upload job with no pending files left.
                try:
                    client.start_run(
                        "manual",
                        metadata={"source": "upload", "ingest": "pending_calls"},
                        run_id=run_id,
                    )
                    client.finish_run(str(run_id), "success", items_processed=0, items_failed=0)
                except Exception as exc:
                    print(f"finish empty job failed: {exc}")
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


def run_upload_batch(
    client,
    stt,
    run_id: str | None,
    language: str,
    force: bool = False,
    only: str | None = None,
    pending_calls: list[dict] | None = None,
) -> int:
    started = client.start_run(
        "manual",
        metadata={"source": "upload", "ingest": "pending_calls"},
        run_id=run_id,
    )
    run_id = str(started["run_id"])
    log("run_start", run_id=run_id, ingest="pending_calls")

    if pending_calls is None:
        try:
            pending = client.get_pending(limit=20)
            pending_calls = list(pending.get("calls") or [])
        except Exception as exc:
            client.log(run_id, "error", f"get_pending failed: {exc}")
            client.finish_run(run_id, "failed", items_processed=0, items_failed=1, error_message=str(exc))
            print(f"get_pending failed: {exc}")
            return 1

    source = get_recording_source(
        "os_pending",
        os_client=client,
        pending_calls=pending_calls,
        cache_dir=ROOT / "inbox" / "upload-cache",
    )
    recordings = source.list_new()
    if only:
        needle = only.lower()
        recordings = [
            rec
            for rec in recordings
            if needle in (rec.remote_id or "").lower() or needle in (rec.name or "").lower()
        ]
        print(f"Filtered to {len(recordings)} file(s) matching {only!r}")

    print(f"Pending uploads: {len(recordings)}")
    if not recordings:
        client.log(run_id, "info", "No pending OS uploads")
        client.finish_run(run_id, "success", items_processed=0, items_failed=0)
        print("Nothing to process. Upload a recording in Liba OS, then run again.")
        return 0

    processed = failed = skipped = 0
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
