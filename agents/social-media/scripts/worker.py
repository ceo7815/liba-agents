"""Watch Liba OS social publish queue and post to Facebook / Instagram."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import log
from shared.os_client import OsError, get_os_client
from shared.secrets import env_value
from shared.social_config import load_social_config
from shared.social_meta import is_dry_run, page_id, page_token
from shared.social_pipeline import publish_claimed, refresh_published


def main() -> int:
    parser = argparse.ArgumentParser(description="social-media Meta publisher")
    parser.add_argument("--once", action="store_true", help="Publish due posts now, then refresh analytics once")
    parser.add_argument("--watch", action="store_true", help="Poll the OS queue until interrupted")
    parser.add_argument(
        "--heartbeat-only",
        action="store_true",
        help="Stay online in Liba OS without claiming the publish queue",
    )
    parser.add_argument("--interval", type=int, default=30, help="Seconds between queue polls")
    parser.add_argument(
        "--analytics-every",
        type=int,
        default=10,
        help="Refresh insights/inbox every N watch cycles",
    )
    args = parser.parse_args()

    cfg = load_social_config()
    os_cfg = cfg["os"]
    slug = cfg["agent"].get("os_slug") or "social-media"
    # Env wins in production (xCloud). config base_url is local-dev fallback only.
    base_url = (env_value("LIBA_OS_BASE_URL") or os_cfg.get("base_url") or "").rstrip("/")
    api_key = env_value("LIBA_OS_API_KEY")
    print(
        f"os_client mode={os_cfg.get('mode') or 'mock'} slug={slug} "
        f"base_url={base_url or '(empty)'} api_key_set={bool(api_key)}"
    )
    if not base_url or "localhost" in base_url or "127.0.0.1" in base_url:
        print(
            "WARNING: OS base_url looks local/empty — set LIBA_OS_BASE_URL to the public Liba OS host",
            file=sys.stderr,
        )
    client = get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=base_url or None,
        api_key=api_key,
        agent_slug=slug,
    )
    report_tools(client)

    if args.watch:
        heartbeat_only = args.heartbeat_only or env_value("SOCIAL_PUBLISH_ENABLED") not in {
            "1",
            "true",
            "yes",
        }
        print(f"Watching every {args.interval}s (Ctrl+C to stop)")
        print(f"dry_run={is_dry_run()} heartbeat_only={heartbeat_only}")
        try:
            return watch_loop(
                client,
                args.interval,
                args.analytics_every,
                heartbeat_only=heartbeat_only,
            )
        except KeyboardInterrupt:
            print("Stopped.")
            return 0

    if not args.once:
        print("Use --once or --watch.", file=sys.stderr)
        return 2

    return run_once(client)


def report_tools(client) -> None:
    meta_ok = bool(page_id() and page_token()) and not is_dry_run()
    tools = [
        (
            "meta-graph",
            "publish",
            "connected" if meta_ok else "disconnected",
            {
                "dry_run": is_dry_run(),
                "page_id_set": bool(page_id()),
                "ig_set": bool(env_value("META_IG_USER_ID")),
            },
        ),
    ]
    for name, tool_type, status, metadata in tools:
        try:
            client.report_tool_status(name, tool_type, status, metadata=metadata)
            print(f"tool {name}: {status}")
        except OsError as exc:
            print(f"tool {name}: report failed: {exc}")


def watch_loop(client, interval: int, analytics_every: int, *, heartbeat_only: bool = False) -> int:
    try:
        client.heartbeat("online")
    except Exception as exc:
        print(f"heartbeat failed: {exc}")
    cycle = 0
    try:
        while True:
            cycle += 1
            if not heartbeat_only:
                drain_queue(client, limit=5)
                if cycle % max(1, analytics_every) == 0:
                    run_analytics(client)
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
    return 0


def run_once(client) -> int:
    processed, failed = drain_queue(client, limit=20)
    run_analytics(client)
    return 0 if failed == 0 else 1 if processed == 0 else 0


def drain_queue(client, *, limit: int) -> tuple[int, int]:
    processed = failed = 0
    for _ in range(limit):
        try:
            due = client.social_poll_due()
        except OsError as exc:
            log("social_poll_error", error=str(exc))
            print(f"social.poll_due failed: {exc}")
            break
        if not due.get("has_work"):
            break
        trigger = str(due.get("trigger") or "schedule").strip() or "schedule"
        if trigger not in {"schedule", "immediate", "manual"}:
            trigger = "schedule"
        started = client.start_run(
            trigger,
            metadata={
                "post_id": (due.get("post") or {}).get("id"),
                "queue_id": due.get("queue_id"),
                "trigger": trigger,
            },
        )
        run_id = str(started["run_id"])
        result = publish_claimed(client, due, run_id=run_id)
        if result.get("ok"):
            processed += 1
            status = "partial" if result.get("partial") else "success"
            client.finish_run(run_id, status, items_processed=1, items_failed=0)
            print(f"published {result.get('post_id')}")
        else:
            failed += 1
            client.finish_run(
                run_id,
                "failed",
                items_processed=0,
                items_failed=1,
                error_message=result.get("error"),
            )
            print(f"failed {result.get('post_id')}: {result.get('error')}")
    if processed or failed:
        print(f"queue: ok={processed} failed={failed}")
    return processed, failed


def run_analytics(client) -> None:
    try:
        started = client.start_run("analytics")
        run_id = str(started["run_id"])
        stats = refresh_published(client, run_id=run_id)
        client.finish_run(
            run_id,
            "success",
            items_processed=stats.get("analytics", 0),
            items_failed=0,
        )
        print(
            f"analytics: posts={stats.get('posts')} saved={stats.get('analytics')} inbox={stats.get('inbox')}"
        )
    except OsError as exc:
        print(f"analytics failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
