"""Liba OS client.

Agents never talk to Supabase. They call POST {LIBA_OS_BASE_URL}/api/mcp
with Bearer API key and { "tool", "params" }.

mode=mock writes the same tool calls to mock_os_data/ (no network).
mode=http is the live OS. Swap via config/env — agent loop stays the same.
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Must match the Liba OS agents.slug seed unless OS renames it.
DEFAULT_AGENT_SLUG = "call-control"


class OsError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OsClient(ABC):
    """One method per OS MCP tool. See docs/os-contract-approval.md."""

    def __init__(self, agent_slug: str = DEFAULT_AGENT_SLUG) -> None:
        self.agent_slug = agent_slug

    @abstractmethod
    def call_tool(self, tool: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        ...

    def start_run(
        self,
        trigger: str,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"agent_slug": self.agent_slug, "trigger": trigger}
        if metadata is not None:
            params["metadata"] = metadata
        if run_id:
            params["run_id"] = run_id
        return self.call_tool("os.start_run", params)

    def poll_work(self) -> dict[str, Any]:
        return self.call_tool("os.poll_work", {"agent_slug": self.agent_slug})

    def heartbeat(self, status: str = "online", run_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"agent_slug": self.agent_slug, "status": status}
        if run_id:
            params["run_id"] = run_id
        return self.call_tool("os.heartbeat", params)

    def finish_run(
        self,
        run_id: str,
        status: str,
        items_processed: int | None = None,
        items_failed: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"run_id": run_id, "status": status}
        for key, value in {
            "items_processed": items_processed,
            "items_failed": items_failed,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "error_message": error_message,
        }.items():
            if value is not None:
                params[key] = value
        return self.call_tool("os.finish_run", params)

    def report_cost(
        self,
        run_id: str,
        service: str,
        cost_usd: float,
        units: float | None = None,
        unit_type: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"run_id": run_id, "service": service, "cost_usd": cost_usd}
        if units is not None:
            params["units"] = units
        if unit_type is not None:
            params["unit_type"] = unit_type
        return self.call_tool("os.report_cost", params)

    def report_tool_status(
        self,
        tool_name: str,
        tool_type: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        agent_slug: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_type": tool_type,
            "status": status,
            "agent_slug": agent_slug or self.agent_slug,
        }
        if metadata is not None:
            params["metadata"] = metadata
        return self.call_tool("os.report_tool_status", params)

    def log(self, run_id: str, level: str, message: str) -> dict[str, Any]:
        return self.call_tool(
            "os.log",
            {"run_id": run_id, "level": level, "message": message},
        )

    def register_call(
        self,
        external_id: str,
        source: str,
        duration_sec: float | None = None,
        call_date: str | None = None,
        audio_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"external_id": external_id, "source": source}
        for key, value in {
            "duration_sec": duration_sec,
            "call_date": call_date,
            "audio_path": audio_path,
            "metadata": metadata,
        }.items():
            if value is not None:
                params[key] = value
        return self.call_tool("calls.register", params)

    def get_pending(self, limit: int = 10) -> dict[str, Any]:
        return self.call_tool("calls.get_pending", {"limit": limit})

    def set_call_status(self, call_id: str, status: str) -> dict[str, Any]:
        return self.call_tool("calls.set_status", {"call_id": call_id, "status": status})

    def save_transcript(
        self,
        call_id: str,
        text: str,
        segments: list[dict[str, Any]] | None = None,
        provider: str | None = None,
        cost_usd: float | None = None,
        language: str = "he",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "call_id": call_id,
            "text": text,
            "full_text": text,
            "language": language,
        }
        if segments is not None:
            params["segments"] = segments
        if provider is not None:
            params["provider"] = provider
        if cost_usd is not None:
            params["cost_usd"] = cost_usd
        return self.call_tool("calls.save_transcript", params)

    def save_analysis(
        self,
        call_id: str,
        run_id: str | None = None,
        summary: str | None = None,
        overall_score: float | None = None,
        rubric_scores: dict[str, Any] | None = None,
        findings: Any = None,
        recommendations: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"call_id": call_id}
        for key, value in {
            "run_id": run_id,
            "summary": summary,
            "overall_score": overall_score,
            "rubric_scores": rubric_scores,
            "findings": findings,
            "recommendations": recommendations,
            "model": model,
        }.items():
            if value is not None:
                params[key] = value
        return self.call_tool("calls.save_analysis", params)

    def social_poll_due(self, run_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"agent_slug": self.agent_slug}
        if run_id:
            params["run_id"] = run_id
        return self.call_tool("social.poll_due", params)

    def social_complete(
        self,
        queue_id: str,
        post_id: str,
        meta_ids: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "queue_id": queue_id,
            "post_id": post_id,
            "meta_ids": meta_ids,
        }
        if run_id:
            params["run_id"] = run_id
        return self.call_tool("social.complete", params)

    def social_fail(self, queue_id: str, post_id: str, error_message: str) -> dict[str, Any]:
        return self.call_tool(
            "social.fail",
            {
                "queue_id": queue_id,
                "post_id": post_id,
                "error_message": error_message,
            },
        )

    def social_list_published(self, limit: int = 20) -> dict[str, Any]:
        return self.call_tool("social.list_published", {"limit": limit})

    def social_save_analytics(self, post_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        params = {"post_id": post_id, **metrics}
        return self.call_tool("social.save_analytics", params)

    def social_inbox_upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("social.inbox_upsert", item)


class MockOsClient(OsClient):
    """Same tools, files on disk. Default until LIBA_OS_* is set."""

    def __init__(self, data_dir: Path | None = None, agent_slug: str = DEFAULT_AGENT_SLUG) -> None:
        super().__init__(agent_slug=agent_slug)
        root = Path(__file__).resolve().parents[1]
        self.data_dir = data_dir or (root / "mock_os_data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "_state.json"

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"queued": False, "queued_run_id": None, "calls": {}, "by_id": {}}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"queued": False, "queued_run_id": None, "calls": {}, "by_id": {}}
        raw.setdefault("queued", False)
        raw.setdefault("queued_run_id", None)
        raw.setdefault("calls", {})
        raw.setdefault("by_id", {})
        return raw

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def queue_work(self) -> str:
        """Simulate the OS button: one queued run."""
        state = self._load_state()
        run_id = state.get("queued_run_id") or str(uuid.uuid4())
        state["queued"] = True
        state["queued_run_id"] = run_id
        self._save_state(state)
        return run_id

    def call_tool(self, tool: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        now = datetime.now(timezone.utc).isoformat()
        state = self._load_state()
        data: dict[str, Any]

        if tool == "os.start_run":
            run_id = str(params.get("run_id") or state.get("queued_run_id") or uuid.uuid4())
            state["queued"] = False
            state["claimed"] = False
            state["queued_run_id"] = None
            self._save_state(state)
            data = {"run_id": run_id, "started_at": now, "status": "running"}
        elif tool == "os.poll_work":
            if state.get("queued") or state.get("claimed"):
                run_id = state.get("queued_run_id") or str(uuid.uuid4())
                state["queued"] = False
                state["claimed"] = True
                state["queued_run_id"] = run_id
                self._save_state(state)
                data = {
                    "has_work": True,
                    "run_id": run_id,
                    "trigger": "manual",
                    "agent_slug": self.agent_slug,
                    "metadata": {"source": "drive"},
                }
            else:
                data = {"has_work": False}
        elif tool == "social.poll_due":
            jobs = state.setdefault("social_queue", [])
            if jobs:
                job = jobs.pop(0)
                self._save_state(state)
                data = {"has_work": True, **job}
            else:
                data = {"has_work": False}
        elif tool == "social.complete":
            data = {
                "queue_id": params.get("queue_id"),
                "post_id": params.get("post_id"),
                "status": "published",
            }
        elif tool == "social.fail":
            data = {
                "queue_id": params.get("queue_id"),
                "post_id": params.get("post_id"),
                "status": "failed",
            }
        elif tool == "social.list_published":
            data = {"posts": state.get("social_published") or []}
        elif tool == "social.save_analytics":
            data = {"post_id": params.get("post_id"), "recorded_at": now}
        elif tool == "social.inbox_upsert":
            data = {
                "id": str(uuid.uuid4()),
                "platform": params.get("platform"),
                "external_id": params.get("external_id"),
                "status": "new",
                "created": True,
            }
        elif tool == "calls.register":
            external_id = str(params.get("external_id") or "")
            existing = state["calls"].get(external_id)
            if existing:
                done = existing.get("status") == "done"
                data = {
                    "id": existing["id"],
                    "call_id": existing["id"],
                    "external_id": external_id,
                    "source": existing.get("source") or params.get("source"),
                    "status": existing.get("status") or "pending",
                    "created": False,
                    "skip_analysis": done,
                }
                if done:
                    data["reason"] = "already_analyzed"
            else:
                call_id = str(uuid.uuid4())
                row = {
                    "id": call_id,
                    "external_id": external_id,
                    "source": params.get("source"),
                    "status": "pending",
                }
                state["calls"][external_id] = row
                state["by_id"][call_id] = external_id
                self._save_state(state)
                data = {**row, "call_id": call_id, "created": True, "skip_analysis": False}
        elif tool == "calls.set_status":
            call_id = str(params.get("call_id") or "")
            status = str(params.get("status") or "")
            ext = state["by_id"].get(call_id)
            if ext and ext in state["calls"]:
                state["calls"][ext]["status"] = status
                self._save_state(state)
            data = {"id": call_id, "status": status}
        elif tool == "calls.save_analysis":
            call_id = str(params.get("call_id") or "")
            ext = state["by_id"].get(call_id)
            if ext and ext in state["calls"]:
                state["calls"][ext]["status"] = "done"
                self._save_state(state)
            data = {"accepted": True, "call_id": call_id, "status": "done"}
        elif tool == "calls.get_pending":
            pending = [row for row in state["calls"].values() if row.get("status") == "pending"]
            data = {"calls": pending, "count": len(pending)}
        else:
            data = {"accepted": True}

        record = {
            "reported_at": now,
            "mode": "mock",
            "tool": tool,
            "params": params,
            "data": data,
        }
        safe = tool.replace(".", "-")
        path = self.data_dir / f"{safe}-{uuid.uuid4().hex[:8]}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        data["_mock_path"] = str(path)
        return data


class HttpOsClient(OsClient):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        agent_slug: str = DEFAULT_AGENT_SLUG,
        timeout_sec: int = 60,
    ) -> None:
        super().__init__(agent_slug=agent_slug)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    def call_tool(self, tool: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"tool": tool, "params": params or {}}).encode("utf-8")
        req = Request(
            f"{self.base_url}/api/mcp",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise OsError(f"OS HTTP {exc.code}: {raw or exc.reason}", status=exc.code) from exc
        except URLError as exc:
            raise OsError(f"OS unreachable: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise OsError(f"OS connection error: {exc}") from exc

        if not isinstance(payload, dict):
            raise OsError("OS returned a non-object JSON body")
        if payload.get("ok") is False:
            raise OsError(str(payload.get("error") or "OS tool failed"))
        if payload.get("ok") is not True:
            raise OsError(f"OS returned unexpected body: {payload!r}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OsError("OS success body missing data object")
        return data


def _hermes_env(name: str) -> str:
    from shared.secrets import env_value

    return env_value(name)


def get_os_client(
    mode: str = "mock",
    *,
    data_dir: Path | str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    agent_slug: str = DEFAULT_AGENT_SLUG,
) -> OsClient:
    if mode == "mock":
        return MockOsClient(
            Path(data_dir) if data_dir else None,
            agent_slug=agent_slug,
        )
    if mode == "http":
        url = base_url or os.environ.get("LIBA_OS_BASE_URL", "") or _hermes_env("LIBA_OS_BASE_URL")
        key = api_key or os.environ.get("LIBA_OS_API_KEY", "") or _hermes_env("LIBA_OS_API_KEY")
        if not url or not key:
            raise OsError("http mode needs LIBA_OS_BASE_URL and LIBA_OS_API_KEY")
        return HttpOsClient(url, key, agent_slug=agent_slug)
    raise ValueError(f"Unknown OS client mode: {mode!r}")
