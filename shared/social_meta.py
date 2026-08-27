"""Meta Graph publisher for Liba Facebook page + Instagram.

Never replies to comments. Page token lives in Hermes profile .env.
If tokens are missing, dry-run records fake ids so the OS calendar can be tested.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shared.secrets import env_value

GRAPH_BASE = "https://graph.facebook.com"


class MetaError(RuntimeError):
    pass


def graph_version() -> str:
    return env_value("META_GRAPH_VERSION") or "v21.0"


def page_id() -> str:
    return env_value("META_PAGE_ID")


def page_token() -> str:
    return env_value("META_PAGE_ACCESS_TOKEN")


def ig_user_id() -> str:
    return env_value("META_IG_USER_ID")


def is_dry_run(force: bool | None = None) -> bool:
    if force is True:
        return True
    if env_value("SOCIAL_DRY_RUN") in {"1", "true", "yes"}:
        return True
    return not (page_id() and page_token())


def _graph(method: str, path: str, fields: dict[str, Any]) -> dict[str, Any]:
    token = page_token()
    if not token:
        raise MetaError("META_PAGE_ACCESS_TOKEN missing")
    payload = {k: v for k, v in fields.items() if v is not None}
    payload["access_token"] = token
    url = f"{GRAPH_BASE}/{graph_version()}/{path.lstrip('/')}"
    data = urlencode(payload).encode("utf-8")
    req = Request(url, data=data, method=method.upper())
    try:
        with urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise MetaError(f"Meta HTTP {exc.code}: {raw or exc.reason}") from exc
    except URLError as exc:
        raise MetaError(f"Meta unreachable: {exc.reason}") from exc
    if not isinstance(body, dict):
        raise MetaError("Meta returned a non-object JSON body")
    if body.get("error"):
        raise MetaError(str(body["error"]))
    return body


def _get(path: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    token = page_token()
    if not token:
        raise MetaError("META_PAGE_ACCESS_TOKEN missing")
    q = dict(fields or {})
    q["access_token"] = token
    url = f"{GRAPH_BASE}/{graph_version()}/{path.lstrip('/')}?{urlencode(q)}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise MetaError(f"Meta HTTP {exc.code}: {raw or exc.reason}") from exc
    except URLError as exc:
        raise MetaError(f"Meta unreachable: {exc.reason}") from exc
    if not isinstance(body, dict):
        raise MetaError("Meta returned a non-object JSON body")
    if body.get("error"):
        raise MetaError(str(body["error"]))
    return body


def pick_asset(assets: list[dict[str, Any]], kinds: list[str]) -> dict[str, Any] | None:
    by_kind = {str(a.get("kind") or ""): a for a in assets if a.get("signed_url")}
    for kind in kinds:
        if kind in by_kind:
            return by_kind[kind]
    for asset in assets:
        if asset.get("signed_url"):
            return asset
    return None


def publish_post(
    *,
    post_id: str,
    caption: str,
    platforms: list[str],
    formats: list[str],
    assets: list[dict[str, Any]],
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Publish the same caption to requested platforms/formats. Never replies."""
    dry = is_dry_run(dry_run)
    meta_ids: dict[str, Any] = {"dry_run": dry}
    errors: list[str] = []

    feed_asset = pick_asset(assets, ["feed", "feed_tall", "original"])
    story_asset = pick_asset(assets, ["story", "original", "feed"])
    want_feed = "feed" in formats or not formats
    want_story = "story" in formats

    if dry:
        if "facebook_page" in platforms:
            if want_feed:
                meta_ids["facebook_feed"] = f"dry-fb-feed-{post_id}"
            if want_story:
                meta_ids["facebook_story"] = f"dry-fb-story-{post_id}"
        if "instagram" in platforms:
            if want_feed:
                meta_ids["instagram_feed"] = f"dry-ig-feed-{post_id}"
            if want_story:
                meta_ids["instagram_story"] = f"dry-ig-story-{post_id}"
        return {"meta_ids": meta_ids, "errors": errors, "dry_run": True}

    pid = page_id()
    ig = ig_user_id()

    if "facebook_page" in platforms and pid:
        if want_feed:
            try:
                meta_ids["facebook_feed"] = _facebook_feed(pid, caption, feed_asset)
            except MetaError as exc:
                errors.append(f"facebook_feed: {exc}")
        if want_story and story_asset:
            try:
                meta_ids["facebook_story"] = _facebook_story(pid, story_asset)
            except MetaError as exc:
                errors.append(f"facebook_story: {exc}")

    if "instagram" in platforms and ig:
        if want_feed:
            if not feed_asset:
                errors.append("instagram_feed: image URL required")
            else:
                try:
                    meta_ids["instagram_feed"] = _instagram_publish(
                        ig, feed_asset, caption, stories=False
                    )
                except MetaError as exc:
                    errors.append(f"instagram_feed: {exc}")
        if want_story:
            if not story_asset:
                errors.append("instagram_story: image URL required")
            else:
                try:
                    meta_ids["instagram_story"] = _instagram_publish(
                        ig, story_asset, caption="", stories=True
                    )
                except MetaError as exc:
                    errors.append(f"instagram_story: {exc}")

    if not any(k for k in meta_ids if k not in {"dry_run"}):
        raise MetaError("; ".join(errors) or "nothing published")
    return {"meta_ids": meta_ids, "errors": errors, "dry_run": False}


def _is_video(asset: dict[str, Any] | None) -> bool:
    mime = str((asset or {}).get("mime_type") or "")
    name = str((asset or {}).get("file_name") or "")
    return mime.startswith("video/") or name.lower().endswith(".mp4")


def _facebook_feed(pid: str, caption: str, asset: dict[str, Any] | None) -> str:
    if asset and _is_video(asset):
        body = _graph("POST", f"{pid}/videos", {"file_url": asset["signed_url"], "description": caption})
        return str(body.get("id") or "")
    if asset and asset.get("signed_url"):
        body = _graph("POST", f"{pid}/photos", {"url": asset["signed_url"], "caption": caption})
        return str(body.get("id") or body.get("post_id") or "")
    body = _graph("POST", f"{pid}/feed", {"message": caption})
    return str(body.get("id") or "")


def _facebook_story(pid: str, asset: dict[str, Any]) -> str:
    photo = _graph(
        "POST",
        f"{pid}/photos",
        {"url": asset["signed_url"], "published": "false"},
    )
    photo_id = photo.get("id")
    if not photo_id:
        raise MetaError("unpublished photo id missing")
    body = _graph("POST", f"{pid}/photo_stories", {"photo_id": photo_id})
    return str(body.get("id") or photo_id)


def _instagram_publish(ig: str, asset: dict[str, Any], caption: str, *, stories: bool) -> str:
    fields: dict[str, Any] = {}
    if _is_video(asset):
        fields["video_url"] = asset["signed_url"]
        fields["media_type"] = "STORIES" if stories else "REELS"
    else:
        fields["image_url"] = asset["signed_url"]
        if stories:
            fields["media_type"] = "STORIES"
    if caption and not stories:
        fields["caption"] = caption
    created = _graph("POST", f"{ig}/media", fields)
    creation_id = created.get("id")
    if not creation_id:
        raise MetaError("IG creation id missing")
    _wait_instagram_container(str(creation_id))
    published = _graph("POST", f"{ig}/media_publish", {"creation_id": creation_id})
    return str(published.get("id") or creation_id)


def _wait_instagram_container(creation_id: str, *, timeout_sec: int = 90) -> None:
    """Instagram containers are often not ready immediately; publish too early fails."""
    deadline = time.time() + timeout_sec
    last_status = ""
    while time.time() < deadline:
        body = _get(creation_id, {"fields": "status_code,status"})
        last_status = str(body.get("status_code") or body.get("status") or "")
        if last_status.upper() == "FINISHED":
            return
        if last_status.upper() in {"ERROR", "EXPIRED"}:
            raise MetaError(f"IG container {creation_id} status={last_status}")
        time.sleep(2)
    raise MetaError(f"IG container {creation_id} not ready (last_status={last_status or 'unknown'})")


def fetch_insights(meta_ids: dict[str, Any]) -> dict[str, int]:
    """Best-effort public metrics. Zeros if dry-run or fields unavailable."""
    metrics = {
        "impressions": 0,
        "reach": 0,
        "likes": 0,
        "comments": 0,
        "saves": 0,
        "shares": 0,
        "link_clicks": 0,
        "story_views": 0,
        "new_followers": 0,
    }
    if meta_ids.get("dry_run") or is_dry_run():
        return metrics

    fb_id = meta_ids.get("facebook_feed")
    if isinstance(fb_id, str) and fb_id:
        try:
            data = _get(str(fb_id), {"fields": "shares,likes.summary(true),comments.summary(true)"})
            likes = data.get("likes") or {}
            comments = data.get("comments") or {}
            shares = data.get("shares") or {}
            metrics["likes"] += int((likes.get("summary") or {}).get("total_count") or 0)
            metrics["comments"] += int((comments.get("summary") or {}).get("total_count") or 0)
            metrics["shares"] += int(shares.get("count") or 0)
        except MetaError:
            pass
        try:
            ins = _get(f"{fb_id}/insights", {"metric": "post_impressions,post_impressions_unique"})
            for row in ins.get("data") or []:
                name = row.get("name")
                values = row.get("values") or [{}]
                value = int((values[0] or {}).get("value") or 0)
                if name == "post_impressions":
                    metrics["impressions"] += value
                if name == "post_impressions_unique":
                    metrics["reach"] += value
        except MetaError:
            pass

    ig_id = meta_ids.get("instagram_feed")
    if isinstance(ig_id, str) and ig_id:
        try:
            ins = _get(
                f"{ig_id}/insights",
                {"metric": "impressions,reach,likes,comments,saved,shares"},
            )
            for row in ins.get("data") or []:
                name = str(row.get("name") or "")
                values = row.get("values") or [{}]
                value = int((values[0] or {}).get("value") or 0)
                if name == "impressions":
                    metrics["impressions"] += value
                elif name == "reach":
                    metrics["reach"] += value
                elif name == "likes":
                    metrics["likes"] += value
                elif name == "comments":
                    metrics["comments"] += value
                elif name == "saved":
                    metrics["saves"] += value
                elif name == "shares":
                    metrics["shares"] += value
        except MetaError:
            pass

    story_id = meta_ids.get("instagram_story")
    if isinstance(story_id, str) and story_id:
        try:
            ins = _get(f"{story_id}/insights", {"metric": "impressions,reach"})
            for row in ins.get("data") or []:
                values = row.get("values") or [{}]
                metrics["story_views"] += int((values[0] or {}).get("value") or 0)
        except MetaError:
            pass

    return metrics


def fetch_comments(meta_ids: dict[str, Any], post_id: str) -> list[dict[str, Any]]:
    """Inbound comments only. The agent must never reply."""
    if meta_ids.get("dry_run") or is_dry_run():
        return []
    items: list[dict[str, Any]] = []
    fb_id = meta_ids.get("facebook_feed")
    if isinstance(fb_id, str) and fb_id:
        try:
            data = _get(str(fb_id), {"fields": "comments.limit(50){id,from,message,created_time}"})
            comments = (data.get("comments") or {}).get("data") or []
            for row in comments:
                frm = row.get("from") or {}
                items.append(
                    {
                        "platform": "facebook_page",
                        "external_id": str(row.get("id") or ""),
                        "post_id": post_id,
                        "author_name": frm.get("name"),
                        "author_handle": frm.get("id"),
                        "body": row.get("message") or "",
                        "received_at": row.get("created_time"),
                    }
                )
        except MetaError:
            pass
    ig_id = meta_ids.get("instagram_feed")
    if isinstance(ig_id, str) and ig_id:
        try:
            data = _get(f"{ig_id}/comments", {"fields": "id,text,username,timestamp"})
            for row in data.get("data") or []:
                items.append(
                    {
                        "platform": "instagram",
                        "external_id": str(row.get("id") or ""),
                        "post_id": post_id,
                        "author_name": row.get("username"),
                        "author_handle": row.get("username"),
                        "body": row.get("text") or "",
                        "received_at": row.get("timestamp"),
                    }
                )
        except MetaError:
            pass
    return [item for item in items if item.get("external_id") and item.get("body")]
