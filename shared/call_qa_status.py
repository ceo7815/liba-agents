"""Report call-qa tools the OS agent-management screen expects."""

from __future__ import annotations

from shared.os_client import OsError
from shared.secrets import env_value
from shared.voicenter import read_runtime_status


def report_call_qa_tools(client) -> None:
    openai_ok = bool(env_value("OPENAI_API_KEY"))
    voicenter_ok = bool(env_value("VOICENTER_API_CODE") or env_value("VOICENTER_EXTENSION"))
    pull = read_runtime_status()
    pull_error = str(pull.get("error") or "").strip()
    if not voicenter_ok:
        voicenter_status = "disconnected"
    elif pull_error:
        voicenter_status = "error"
    else:
        voicenter_status = "connected"
    tools = [
        (
            "voicenter",
            "source",
            voicenter_status,
            {
                "extension": env_value("VOICENTER_EXTENSION") or "LvMpqlBj",
                "agent": "סופיה",
                "last_pull_accepted": pull.get("accepted"),
                "last_pull_error": pull_error or None,
            },
        ),
        (
            "openai",
            "llm",
            "connected" if openai_ok else "disconnected",
            {"model": "gpt-5.4-mini"},
        ),
    ]
    for name, tool_type, status, metadata in tools:
        try:
            client.report_tool_status(name, tool_type, status, metadata=metadata)
            print(f"tool {name}: {status}")
        except OsError as exc:
            print(f"tool {name}: report failed: {exc}")
