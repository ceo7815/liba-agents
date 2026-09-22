"""Process Voicenter PUSH inbox (Sofia) through call-qa pipeline."""

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
from shared.os_client import get_os_client
from shared.pipeline import process_recording
from shared.sources import get_recording_source
from shared.stt import get_stt_provider
from shared.call_qa_status import report_call_qa_tools
from shared.os_client import OsError
from shared.secrets import env_value
from shared.voicenter import list_pending_inbox, load_inbox_file, mark_processed, sofia_agent_name


def process_inbox_once(*, force: bool = False) -> int:
    cfg = load_call_qa_config()
    os_cfg = cfg["os"]
    stt_cfg = cfg["stt"]
    slug = cfg["agent"].get("os_slug") or "call-control"

    client = get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=env_value("LIBA_OS_BASE_URL") or os_cfg.get("base_url"),
        api_key=env_value("LIBA_OS_API_KEY"),
        agent_slug=slug,
    )
    stt = get_stt_provider(stt_cfg.get("provider"))
    source = get_recording_source("voicenter")

    pending_files = list_pending_inbox()
    if not pending_files:
        return 0
    recordings = {r.remote_id: r for r in source.list_new()}

    run = client.start_run(trigger="voicenter_push")
    run_id = str(run.get("run_id") or run.get("id") or "voicenter")
    language = stt_cfg.get("language") or "auto"
    print(f"voicenter inbox: {len(pending_files)} files, {len(recordings)} accepted for {sofia_agent_name()}")

    ok = skipped = failed = 0
    for path in pending_files:
        try:
            call, envelope = load_inbox_file(path)
        except Exception as exc:
            print(f"skip bad file {path.name}: {exc}")
            mark_processed(path)
            failed += 1
            continue
        if envelope.get("accepted") is False:
            print(f"ignore {path.name}: {envelope.get('reason')}")
            mark_processed(path)
            skipped += 1
            continue
        recording = recordings.get(call.call_id)
        if recording is None:
            print(f"ignore {path.name}: not in accepted list")
            mark_processed(path)
            skipped += 1
            continue
        result = process_recording(
            client,
            source,
            stt,
            recording,
            run_id=run_id,
            language=language,
            force=force,
        )
        print(f"{call.call_id}: {result}")
        if result == "ok":
            ok += 1
            mark_processed(path)
        elif result == "skipped":
            skipped += 1
            mark_processed(path)
        else:
            failed += 1
            # leave in inbox for retry

    try:
        client.finish_run(run_id, status="done" if failed == 0 else "partial")
    except Exception as exc:
        log("finish_run_error", error=str(exc))
    print(f"done ok={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Voicenter → call-qa worker (Sofia)")
    parser.add_argument("--once", action="store_true", help="Process inbox once")
    parser.add_argument("--watch", action="store_true", help="Poll inbox forever")
    parser.add_argument("--interval", type=int, default=15, help="Seconds between polls")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if done")
    args = parser.parse_args()

    if args.watch:
        cfg = load_call_qa_config()
        os_cfg = cfg["os"]
        slug = cfg["agent"].get("os_slug") or "call-control"
        client = get_os_client(
            os_cfg.get("mode") or "mock",
            data_dir=os_cfg.get("_mock_dir"),
            base_url=env_value("LIBA_OS_BASE_URL") or os_cfg.get("base_url"),
            api_key=env_value("LIBA_OS_API_KEY"),
            agent_slug=slug,
        )
        report_call_qa_tools(client)
        print(f"Watching Voicenter inbox every {args.interval}s; heartbeat every 60s")
        last_beat = 0.0
        try:
            while True:
                now = time.time()
                if now - last_beat >= 60:
                    try:
                        client.heartbeat("online")
                        report_call_qa_tools(client)
                        print("heartbeat online")
                    except OsError as exc:
                        print(f"heartbeat failed: {exc}")
                    last_beat = now
                process_inbox_once(force=args.force)
                time.sleep(max(3, args.interval))
        except KeyboardInterrupt:
            print("Stopped.")
            return 0

    if not args.once:
        print("Use --once or --watch", file=sys.stderr)
        return 2
    return process_inbox_once(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
