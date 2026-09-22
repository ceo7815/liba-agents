"""Push call-qa tool chips to Liba OS immediately (do not wait for history)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.agent_config import load_call_qa_config
from shared.call_qa_status import report_call_qa_tools
from shared.os_client import get_os_client
from shared.secrets import env_value


def main() -> int:
    cfg = load_call_qa_config()
    os_cfg = cfg["os"]
    slug = cfg["agent"].get("os_slug") or "call-control"
    client = get_os_client(
        os_cfg.get("mode") or "mock",
        data_dir=os_cfg.get("_mock_dir"),
        base_url=env_value("LIBA_OS_BASE_URL") or os_cfg.get("base_url"),
        api_key=env_value("LIBA_OS_API_KEY"),
        agent_slug=slug,
    )
    report_call_qa_tools(client)
    try:
        client.heartbeat("online")
        print("heartbeat online")
    except Exception as exc:
        print(f"heartbeat failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
