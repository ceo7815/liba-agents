"""Exact statuses and report shape from the agency checklist (§2, §24, §25)."""

from __future__ import annotations

from typing import Any, Literal

STATUSES = (
    "בוצע",
    "בוצע חלקית",
    "לא בוצע",
    "לא רלוונטי",
    "לא ניתן לאימות",
)

Status = Literal["בוצע", "בוצע חלקית", "לא בוצע", "לא רלוונטי", "לא ניתן לאימות"]
Severity = Literal["קריטי", "מהותי", "לשיפור"]

STATUS_POINTS = {
    "בוצע": 1.0,
    "בוצע חלקית": 0.5,
    "לא בוצע": 0.0,
}

# Checklist chapter → score bucket (from §24 dimension names).
COMPLIANCE_CHAPTERS = (4, 5, 12, 13, 16, 17, 18, 19, 20)
PROFESSIONALISM_CHAPTERS = (6, 7, 8, 9, 10, 11, 14, 15)
QUALITY_CHAPTERS = (21, 22)

BUCKET_CAPS = {
    "compliance": 60,
    "professionalism": 25,
    "quality": 15,
}


def score_bucket(items: list[dict[str, Any]], cap: int) -> float | None:
    """Average of scored items × cap. None if no items in the denominator."""
    scored = [STATUS_POINTS[i["status"]] for i in items if i.get("status") in STATUS_POINTS]
    if not scored:
        return None
    return round((sum(scored) / len(scored)) * cap, 1)


def combine_total(
    compliance: float | None,
    professionalism: float | None,
    quality: float | None,
) -> float | None:
    parts = [
        (compliance, BUCKET_CAPS["compliance"]),
        (professionalism, BUCKET_CAPS["professionalism"]),
        (quality, BUCKET_CAPS["quality"]),
    ]
    present = [(v, cap) for v, cap in parts if v is not None]
    if not present:
        return None
    if len(present) == 3:
        return round(sum(v for v, _ in present))
    cap_sum = sum(cap for _, cap in present)
    raw = sum(v for v, _ in present)
    return round((raw / cap_sum) * 100)
