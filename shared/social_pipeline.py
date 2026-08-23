"""Publish due Liba social posts. OS is source of truth. Never auto-reply."""

from __future__ import annotations

from typing import Any

from shared.logging import log
from shared.os_client import OsClient
from shared.social_meta import MetaError, fetch_comments, fetch_insights, is_dry_run, publish_post


def publish_claimed(client: OsClient, due: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    post = due.get("post") or {}
    assets = due.get("assets") or []
    queue_id = str(due.get("queue_id") or "")
    post_id = str(post.get("id") or "")
    caption = str(post.get("caption") or "").strip()
    platforms = list(post.get("platforms") or [])
    formats = list(post.get("formats") or ["feed"])

    log(
        "social_claim",
        queue_id=queue_id,
        post_id=post_id,
        platforms=platforms,
        formats=formats,
        dry_run=is_dry_run(),
        run_id=run_id,
    )
    client.log(
        run_id,
        "info",
        f"publishing {post_id} platforms={platforms} formats={formats} dry_run={is_dry_run()}",
    )

    try:
        result = publish_post(
            post_id=post_id,
            caption=caption,
            platforms=platforms,
            formats=formats,
            assets=assets,
        )
        meta_ids = result["meta_ids"]
        if result.get("errors"):
            meta_ids["partial_errors"] = result["errors"]
            client.log(run_id, "warn", "; ".join(result["errors"]))
        client.social_complete(queue_id, post_id, meta_ids, run_id=run_id)
        log("social_published", post_id=post_id, dry_run=result.get("dry_run"))
        return {"ok": True, "post_id": post_id, "meta_ids": meta_ids, "partial": bool(result.get("errors"))}
    except (MetaError, Exception) as exc:
        message = str(exc)
        try:
            client.social_fail(queue_id, post_id, message)
        except Exception as fail_exc:
            log("social_fail_report_error", error=str(fail_exc))
        client.log(run_id, "error", message)
        log("social_failed", post_id=post_id, error=message)
        return {"ok": False, "post_id": post_id, "error": message}


def refresh_published(client: OsClient, *, run_id: str, limit: int = 15) -> dict[str, int]:
    listed = client.social_list_published(limit=limit)
    posts = listed.get("posts") or []
    comments_n = 0
    analytics_n = 0
    for post in posts:
        post_id = str(post.get("id") or "")
        meta_ids = post.get("meta_ids") or {}
        if not post_id or not isinstance(meta_ids, dict):
            continue
        metrics = fetch_insights(meta_ids)
        metrics["topic_key"] = post.get("holiday_key") or "general"
        formats = post.get("formats") or ["feed"]
        metrics["format"] = formats[0] if formats else "feed"
        try:
            client.social_save_analytics(post_id, metrics)
            analytics_n += 1
        except Exception as exc:
            log("social_analytics_error", post_id=post_id, error=str(exc))
        for item in fetch_comments(meta_ids, post_id):
            try:
                client.social_inbox_upsert(item)
                comments_n += 1
            except Exception as exc:
                log("social_inbox_error", error=str(exc))
    client.log(
        run_id,
        "info",
        f"analytics posts={len(posts)} saved={analytics_n} inbox={comments_n}",
    )
    return {"posts": len(posts), "analytics": analytics_n, "inbox": comments_n}
