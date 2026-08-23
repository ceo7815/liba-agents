"""Load secrets from Hermes profile .env without printing them."""

from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return Path(raw)
    local = Path.home() / "AppData" / "Local" / "hermes"
    return local if local.exists() else Path.home() / ".hermes"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    home = hermes_home()
    for path in (
        home / "profiles" / "social-media" / ".env",
        home / "profiles" / "call-qa" / ".env",
        home / ".env",
    ):
        value = _parse_env_file(path).get(name, "")
        if value:
            return value
    return ""


def openai_api_key() -> str:
    key = env_value("OPENAI_API_KEY")
    if key:
        return key
    raise RuntimeError("OPENAI_API_KEY not found in environment or Hermes profile .env")
