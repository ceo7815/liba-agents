"""Load agents/call-qa/config.yaml and resolve relative paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agents" / "call-qa"
CONFIG_PATH = AGENT_DIR / "config.yaml"


def load_call_qa_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    source = data.setdefault("source", {})
    os_cfg = data.setdefault("os", {})
    stt = data.setdefault("stt", {})

    local_dir = source.get("local_dir") or "../../inbox"
    cache_dir = (source.get("drive") or {}).get("cache_dir") or "../../inbox/drive-cache"
    mock_dir = os_cfg.get("mock_dir") or "../../mock_os_data"

    source["_local_dir"] = _resolve(local_dir)
    source["_cache_dir"] = _resolve(cache_dir)
    os_cfg["_mock_dir"] = _resolve(mock_dir)
    stt.setdefault("provider", "openai")
    stt.setdefault("language", "he")
    return data


def _resolve(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (AGENT_DIR / path).resolve()
