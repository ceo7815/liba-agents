"""Company packs that cannot complete Layer 2 yet.

Layer 1 (טופס 11.14) always runs. Layer 2 (דף כיסויים) is skipped when
the identified insurer is איילון (partial) or הכשרה (missing).
הראל is complete and must not be blocked.
"""

from __future__ import annotations

from typing import Any

INCOMPLETE_PACKS: tuple[dict[str, Any], ...] = (
    {
        "name": "איילון",
        "aliases": ("איילון", "ayalon"),
        "reason": "חסרים עמודי צ׳ק־ליסט 1, 3, 4",
        "status": "partial",
    },
    {
        "name": "הכשרה",
        "aliases": ("הכשרה", "hachshara"),
        "reason": "אין דף כיסויים",
        "status": "missing",
    },
)

INCOMPLETE_GAP_MARK = "ניתוח לא מלא"


def _haystack(analysis: dict[str, Any]) -> str:
    parts: list[str] = []
    scores = analysis.get("rubric_scores")
    findings = analysis.get("findings")
    ident: dict[str, Any] = {}
    if isinstance(scores, dict) and isinstance(scores.get("identification"), dict):
        ident.update(scores["identification"])
    if isinstance(findings, dict) and isinstance(findings.get("identification"), dict):
        ident.update(findings["identification"])
    for key in ("insurer", "company", "חברה", "חברת_ביטוח"):
        value = ident.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("products_discussed", "products_offered", "products_purchased"):
        value = ident.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value if v)
        elif isinstance(value, str):
            parts.append(value)
    summary = analysis.get("summary")
    if isinstance(summary, str):
        parts.append(summary)
    return " ".join(parts).lower()


def detect_incomplete_insurers(analysis: dict[str, Any]) -> list[dict[str, str]]:
    text = _haystack(analysis)
    hits: list[dict[str, str]] = []
    for pack in INCOMPLETE_PACKS:
        if any(alias.lower() in text for alias in pack["aliases"]):
            hits.append(
                {
                    "name": pack["name"],
                    "reason": pack["reason"],
                    "status": pack["status"],
                }
            )
    return hits


def normalize_findings(analysis: dict[str, Any]) -> dict[str, Any]:
    findings = analysis.get("findings")
    if isinstance(findings, list):
        gaps = []
        for item in findings:
            if isinstance(item, dict) and item.get("what"):
                gaps.append(item)
            elif isinstance(item, str) and item.strip():
                gaps.append({"what": item.strip()})
        analysis["findings"] = {"schema_version": 1, "gaps": gaps}
    elif not isinstance(findings, dict):
        analysis["findings"] = {"schema_version": 1}
    else:
        findings.setdefault("schema_version", 1)
    return analysis


def apply_incomplete_gate(analysis: dict[str, Any]) -> dict[str, Any]:
    """Mark Layer 2 incomplete when Ayalon / Hachshara were identified."""
    normalize_findings(analysis)
    findings = analysis["findings"]
    hits = detect_incomplete_insurers(analysis)
    if not hits:
        findings["analysis_complete"] = True
        findings["analysis_incomplete"] = False
        findings["incomplete_insurers"] = []
        findings.setdefault("layer2_status", "ready")
        return analysis

    names = [hit["name"] for hit in hits]
    findings["analysis_complete"] = False
    findings["analysis_incomplete"] = True
    findings["incomplete_insurers"] = names
    findings["incomplete_reason"] = " · ".join(f"{hit['name']}: {hit['reason']}" for hit in hits)
    findings["layer2_status"] = "skipped_missing_pack"
    findings["company_pack_status"] = hits[0]["status"]

    ident = findings.get("identification")
    if not isinstance(ident, dict):
        ident = {}
        findings["identification"] = ident
    if not ident.get("insurer"):
        ident["insurer"] = " + ".join(names)

    gap_text = (
        f"{INCOMPLETE_GAP_MARK} — זוהתה חברת {', '.join(names)}. "
        "שכבת 11.14 רצה; דף הכיסויים של החברה חסר או חלקי עד השלמת הצ׳ק־ליסט."
    )
    gaps = findings.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    if not any(isinstance(g, dict) and INCOMPLETE_GAP_MARK in str(g.get("what", "")) for g in gaps):
        gaps.insert(
            0,
            {
                "what": gap_text,
                "why_important": "בלי דף הכיסויים אי אפשר לאמת תנאי מוצר של אותה חברה",
                "should_have": "להשלים את חבילת הצ׳ק־ליסט ואז לנתח שוב",
            },
        )
    findings["gaps"] = gaps
    return analysis
