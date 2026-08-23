"""Load agents/social-media/config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agents" / "social-media"
CONFIG_PATH = AGENT_DIR / "config.yaml"


def load_social_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    os_cfg = data.setdefault("os", {})
    mock_dir = os_cfg.get("mock_dir") or "../../mock_os_data"
    os_cfg["_mock_dir"] = _resolve(mock_dir)
    data.setdefault("agent", {})
    data["agent"].setdefault("os_slug", "social-media")
    return data


def _resolve(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (AGENT_DIR / path).resolve()
