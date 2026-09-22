"""HTTP probe + Voicenter CDR PUSH webhook (Sofia filter)."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
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
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"OS probe failed: {exc}"}

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return {"ok": False, "error": f"unexpected OS body: {payload!r}"}
    return {"ok": True, "data": payload.get("data") or {}}


def build_status() -> dict:
    base = (os.environ.get("LIBA_OS_BASE_URL") or "").rstrip("/")
    probe = _probe_os_heartbeat()
    inbox_count = None
    try:
        from pathlib import Path

        inbox = Path("/app/inbox/voicenter")
        if not inbox.exists():
            inbox = Path(__file__).resolve().parents[1] / "inbox" / "voicenter"
        inbox_count = len(list(inbox.glob("*.json"))) if inbox.exists() else 0
    except Exception:
        inbox_count = None
    return {
        "service": "liba-agents",
        "liba_os_base_url": base or None,
        "liba_os_api_key_set": _env_set("LIBA_OS_API_KEY"),
        "social_publish_enabled": os.environ.get("SOCIAL_PUBLISH_ENABLED", "0"),
        "social_dry_run": os.environ.get("SOCIAL_DRY_RUN", "1"),
        "meta_page_id_set": _env_set("META_PAGE_ID"),
        "meta_page_token_set": _env_set("META_PAGE_ACCESS_TOKEN"),
        "meta_ig_user_id_set": _env_set("META_IG_USER_ID"),
        "voicenter_api_code_set": _env_set("VOICENTER_API_CODE"),
        "voicenter_extension": os.environ.get("VOICENTER_EXTENSION") or "LvMpqlBj",
        "voicenter_webhook_token_set": _env_set("VOICENTER_WEBHOOK_TOKEN"),
        "voicenter_inbox_pending": inbox_count,
        "call_qa_voicenter_enabled": os.environ.get("CALL_QA_VOICENTER_ENABLED", "0"),
        "heartbeat_probe": probe,
    }


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = (os.environ.get("VOICENTER_WEBHOOK_TOKEN") or "").strip()
    if not expected:
        return True
    parsed = urlparse(handler.path or "/")
    qs = parse_qs(parsed.query or "")
    token = (qs.get("token") or [None])[0]
    header = handler.headers.get("X-Voicenter-Token") or handler.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        header = header[7:].strip()
    return token == expected or header == expected


def _handle_voicenter_cdr(handler: BaseHTTPRequestHandler) -> None:
    if not _authorized(handler):
        _json_response(handler, 401, {"ok": False, "error": "unauthorized"})
        return
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        # form-urlencoded fallback: keep raw string map if possible
        try:
            from urllib.parse import parse_qsl

            payload = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        except Exception:
            _json_response(handler, 400, {"ok": False, "error": "invalid_json"})
            return
    if not isinstance(payload, dict):
        _json_response(handler, 400, {"ok": False, "error": "payload_must_be_object"})
        return

    # Ensure project root imports work when health.py runs from /app/deploy
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from shared.voicenter import parse_cdr, save_inbox_payload, should_accept

    ok, reason = should_accept(payload)
    path = save_inbox_payload(payload)
    call = parse_cdr(payload)
    _json_response(
        handler,
        200,
        {
            "ok": True,
            "accepted": ok,
            "reason": reason,
            "call_id": call.call_id,
            "has_transcript": call.has_transcript,
            "queued_path": str(path) if path else None,
        },
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path or "/")
        path = parsed.path
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

    def do_POST(self):
        parsed = urlparse(self.path or "/")
        path = parsed.path.rstrip("/")
        if path in {"/webhooks/voicenter/cdr", "/webhook/voicenter/cdr", "/voicenter/cdr"}:
            _handle_voicenter_cdr(self)
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
