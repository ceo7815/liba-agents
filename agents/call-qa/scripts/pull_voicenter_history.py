"""Backfill Sofia answered CDRs from Voicenter Call Log into the inbox / OS."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.agent_config import load_call_qa_config
from shared.os_client import get_os_client
from shared.secrets import env_value
from shared.voicenter import (
    fetch_cdr_pull,
    is_answered,
    parse_cdr,
    save_inbox_payload,
    should_accept,
    sofia_agent_name,
    sofia_extension,
    write_runtime_status,
)


def iter_windows(total_days: int, chunk: int = 7):
    end = datetime.now(timezone.utc)
    left = max(1, total_days)
    while left > 0:
        span = min(chunk, left)
        start = end - timedelta(days=span)
        yield start, end
        end = start
        left -= span


def pull_window(start: datetime, end: datetime) -> list[dict]:
    rows = fetch_cdr_pull(start=start, end=end, use_extension_filter=True)
    if rows:
        return rows
    return fetch_cdr_pull(start=start, end=end, use_extension_filter=False)


def os_client():
    cfg = load_call_qa_config()
    os_cfg = cfg["os"]
    slug = cfg["agent"].get("os_slug") or "call-control"
    return get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=env_value("LIBA_OS_BASE_URL") or os_cfg.get("base_url"),
        api_key=env_value("LIBA_OS_API_KEY") or os.environ.get("LIBA_OS_API_KEY"),
        agent_slug=slug,
    )


def register_row(client, row: dict) -> None:
    call = parse_cdr(row)
    client.register_call(
        external_id=call.call_id,
        source="voicenter",
        duration_sec=call.duration_sec,
        call_date=call.call_date,
        audio_path=call.record_url,
        agent_name=call.agent_name or sofia_agent_name(),
        metadata={
            "agent_name": call.agent_name or sofia_agent_name(),
            "voicenter_call_id": call.call_id,
            "file_name": f"sofia-{call.call_id}",
            "display_name": "לקוח לא זוהה",
            "customer_name": None,
            "caller_phone": call.caller,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Sofia Voicenter history")
    parser.add_argument("--days", type=int, default=90, help="How far back to pull")
    parser.add_argument("--chunk", type=int, default=1, help="Days per Call Log request")
    args = parser.parse_args()

    print(f"history pull: agent={sofia_agent_name()} ext={sofia_extension()} days={args.days}")
    write_runtime_status({"phase": "history", "error": None, "accepted": 0, "rows": 0})
    client = os_client()

    accepted = skipped = registered = total_rows = 0
    last_error = None
    for start, end in iter_windows(args.days, args.chunk):
        label = f"{start.date()}..{end.date()}"
        try:
            rows = pull_window(start, end)
        except Exception as exc:
            last_error = str(exc)
            write_runtime_status(
                {
                    "phase": "history",
                    "error": last_error,
                    "window": label,
                    "accepted": accepted,
                    "registered": registered,
                    "rows": total_rows,
                }
            )
            print(f"PULL failed {label}: {exc}")
            continue
        total_rows += len(rows)
        window_ok = 0
        for row in rows:
            ok, _reason = should_accept(row)
            if not ok or not is_answered(row):
                skipped += 1
                continue
            path = save_inbox_payload(row)
            if not path:
                skipped += 1
                continue
            accepted += 1
            window_ok += 1
            try:
                register_row(client, row)
                registered += 1
            except Exception as exc:
                print(f"register failed {parse_cdr(row).call_id}: {exc}")
        write_runtime_status(
            {
                "phase": "history",
                "error": last_error,
                "window": label,
                "accepted": accepted,
                "registered": registered,
                "rows": total_rows,
            }
        )
        print(f"{label}: rows={len(rows)} accepted+={window_ok}")

    write_runtime_status(
        {
            "phase": "history_done",
            "error": last_error,
            "accepted": accepted,
            "registered": registered,
            "skipped": skipped,
            "rows": total_rows,
        }
    )
    print(f"cdr_rows={total_rows} accepted={accepted} registered={registered} skipped={skipped}")
    print("next: python agents/call-qa/scripts/pull_voicenter.py --once")
    return 0 if accepted or total_rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
