"""Copy to a new agent, then implement. Do not run the template."""

from __future__ import annotations

import sys


def main() -> int:
    print("This is the agent template. Copy agents/_template to agents/<name> first.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
