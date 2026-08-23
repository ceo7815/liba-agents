"""Small structured logger for agent scripts. Not a replacement for Hermes logs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def log(event: str, **fields: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    sys.stderr.write(json.dumps(row, ensure_ascii=False) + "\n")
