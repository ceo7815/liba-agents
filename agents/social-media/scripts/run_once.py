"""Alias for worker.py --once."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if "--once" not in sys.argv and "--watch" not in sys.argv:
    sys.argv.insert(1, "--once")

runpy.run_path(str(Path(__file__).resolve().parent / "worker.py"), run_name="__main__")
