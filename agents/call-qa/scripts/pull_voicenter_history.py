"""Backfill Sofia answered CDRs from Voicenter Call Log into the inbox / OS."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.voicenter import (
    fetch_cdr_pull,
    is_answered,
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
    # Extension filter can miss Sofia if Cpanel stores a different SIP id.
    return fetch_cdr_pull(start=start, end=end, use_extension_filter=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Sofia Voicenter history")
    parser.add_argument("--days", type=int, default=90, help="How far back to pull")
    parser.add_argument("--chunk", type=int, default=7, help="Days per Call Log request")
    args = parser.parse_args()

    print(f"history pull: agent={sofia_agent_name()} ext={sofia_extension()} days={args.days}")
    write_runtime_status({"phase": "history", "error": None, "accepted": 0, "rows": 0})

    accepted = skipped = total_rows = 0
    try:
        for start, end in iter_windows(args.days, args.chunk):
            label = f"{start.date()}..{end.date()}"
            try:
                rows = pull_window(start, end)
            except Exception as exc:
                write_runtime_status(
                    {
                        "phase": "history",
                        "error": str(exc),
                        "window": label,
                        "accepted": accepted,
                        "rows": total_rows,
                    }
                )
                print(f"PULL failed {label}: {exc}")
                return 2
            total_rows += len(rows)
            window_ok = 0
            for row in rows:
                ok, _reason = should_accept(row)
                if not ok or not is_answered(row):
                    skipped += 1
                    continue
                path = save_inbox_payload(row)
                if path:
                    accepted += 1
                    window_ok += 1
                else:
                    skipped += 1
            write_runtime_status(
                {
                    "phase": "history",
                    "error": None,
                    "window": label,
                    "accepted": accepted,
                    "rows": total_rows,
                }
            )
            print(f"{label}: rows={len(rows)} accepted+={window_ok}")
    except Exception as exc:
        write_runtime_status({"phase": "history", "error": str(exc), "accepted": accepted, "rows": total_rows})
        print(f"PULL failed: {exc}")
        return 2

    write_runtime_status(
        {
            "phase": "history_done",
            "error": None,
            "accepted": accepted,
            "skipped": skipped,
            "rows": total_rows,
        }
    )
    print(f"cdr_rows={total_rows} accepted={accepted} skipped={skipped}")
    print("next: python agents/call-qa/scripts/pull_voicenter.py --once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
