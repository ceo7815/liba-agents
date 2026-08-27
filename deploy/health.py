"""HTTP probe for xCloud + browser-friendly /status diagnostics (no secrets)."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _env_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _probe_os_heartbeat() -> dict:
    base = (os.environ.get("LIBA_OS_BASE_URL") or "").rstrip("/")
    key = (os.environ.get("LIBA_OS_API_KEY") or "").strip()
    if not base or not key:
        return {
            "ok": False,
            "error": "missing LIBA_OS_BASE_URL or LIBA_OS_API_KEY in container env",
        }
    body = json.dumps(
        {
            "tool": "os.heartbeat",
            "params": {"agent_slug": "social-media", "status": "online"},
        }
    ).encode("utf-8")
    req = Request(
        f"{base}/api/mcp",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"OS HTTP {exc.code}: {raw or exc.reason}"}
    except URLError as exc:
        return {"ok": False, "error": f"OS unreachable: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001 — surface any probe failure in /status
        return {"ok": False, "error": f"OS probe failed: {exc}"}

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return {"ok": False, "error": f"unexpected OS body: {payload!r}"}
    return {"ok": True, "data": payload.get("data") or {}}


def build_status() -> dict:
    base = (os.environ.get("LIBA_OS_BASE_URL") or "").rstrip("/")
    probe = _probe_os_heartbeat()
    return {
        "service": "liba-agents",
        "liba_os_base_url": base or None,
        "liba_os_api_key_set": _env_set("LIBA_OS_API_KEY"),
        "social_publish_enabled": os.environ.get("SOCIAL_PUBLISH_ENABLED", "0"),
        "social_dry_run": os.environ.get("SOCIAL_DRY_RUN", "1"),
        "meta_page_id_set": _env_set("META_PAGE_ID"),
        "meta_page_token_set": _env_set("META_PAGE_ACCESS_TOKEN"),
        "meta_ig_user_id_set": _env_set("META_IG_USER_ID"),
        "heartbeat_probe": probe,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        path = (self.path or "/").split("?", 1)[0]
        if path in {"/status", "/status.json"}:
            payload = json.dumps(build_status(), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        body = b"liba-agents ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
