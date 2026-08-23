"""Cost ledger written next to each mock OS report. No billing system here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostEvent:
    kind: str
    units: float
    unit_label: str
    usd: float
    note: str = ""


@dataclass
class CostReport:
    events: list[CostEvent] = field(default_factory=list)

    def add(self, event: CostEvent) -> None:
        self.events.append(event)

    def total_usd(self) -> float:
        return round(sum(e.usd for e in self.events), 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_usd": self.total_usd(),
            "events": [
                {
                    "kind": e.kind,
                    "units": e.units,
                    "unit_label": e.unit_label,
                    "usd": e.usd,
                    "note": e.note,
                }
                for e in self.events
            ],
        }
