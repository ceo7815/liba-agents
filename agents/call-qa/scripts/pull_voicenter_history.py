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
)


def chunked_days(total_days: int, chunk: int = 30) -> list[int]:
    left = total_days
    out: list[int] = []
    while left > 0:
        out.append(min(chunk, left))
        left -= chunk
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Sofia Voicenter history")
    parser.add_argument("--days", type=int, default=365, help="How far back to pull")
    args = parser.parse_args()

    print(f"history pull: agent={sofia_agent_name()} ext={sofia_extension()} days={args.days}")
    try:
        rows = fetch_cdr_pull(days=args.days)
    except Exception as exc:
        print(f"PULL failed: {exc}")
        return 2
    accepted = skipped = 0
    for row in rows:
        ok, reason = should_accept(row)
        if not ok or not is_answered(row):
            skipped += 1
            continue
        path = save_inbox_payload(row)
        if path:
            accepted += 1
        else:
            skipped += 1
    print(f"cdr_rows={len(rows)} accepted={accepted} skipped={skipped}")
    print("next: python agents/call-qa/scripts/pull_voicenter.py --once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
