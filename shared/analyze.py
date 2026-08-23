"""Score a diarized transcript with the agency checklist only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "agents" / "call-qa" / "skills" / "call-qa-rubric" / "SKILL.md"
ANALYZE_MODEL = "gpt-5.4-mini"

SYSTEM = """אתה סוכן בקרת שיחות ביטוח. עובד רק לפי הצ'ק-ליסט ב-skill.
אסור להמציא משפטים. אסור להניח פעולות מחוץ לתמלול.
החזר JSON בלבד, בלי markdown.
customer_name ו-agent_name חייבים להיות שמות אדם שנאמרו בתמלול בלבד.
אסור לשים שם סטטוס כמו תקין, חלקי, כשל, לשיפור, קריטי."""

USER_TEMPLATE = """# צ'ק-ליסט
{skill}

# תמלול (עם דוברים)
{transcript}

החזר JSON עם השדות:
{{
  "overall_score": number or null,
  "customer_name": "שם פרטי של הלקוח כפי שנאמר, או null",
  "agent_name": "שם פרטי של הנציג כפי שנאמר, או null",
  "rubric_scores": {{
    "compliance_60": number or null,
    "professionalism_25": number or null,
    "quality_15": number or null,
    "identification": {{
      "customer_name": "שם הלקוח או null",
      "rep_name": "שם הנציג או null"
    }},
    "checklist": []
  }},
  "findings": [],
  "recommendations": [],
  "summary": "string"
}}
"""

PEOPLE_SYSTEM = """חלץ שמות אדם בלבד מתוך תמלול שיחת ביטוח.
החזר JSON בלבד: {"customer_name": string|null, "agent_name": string|null}
כללים:
- customer_name = שם הלקוח / המבוטח שנאמר בשיחה.
- agent_name = שם הנציג/הסוכן של ליבה שנאמר בשיחה.
- אסור להמציא שם שלא נאמר.
- אסור להחזיר מילות סטטוס: תקין, חלקי, כשל, לשיפור, קריטי, לקוח, סוכן, נציג.
- אם נאמר רק שם פרטי — החזר אותו.
- אם לא נאמר במפורש — null."""


def analyze_transcript(transcript_text: str) -> dict[str, Any]:
    from openai import OpenAI

    from shared.secrets import openai_api_key

    skill = SKILL_PATH.read_text(encoding="utf-8")
    client = OpenAI(api_key=openai_api_key(), timeout=180)
    response = client.chat.completions.create(
        model=ANALYZE_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(skill=skill, transcript=transcript_text),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    usage = response.usage
    data["_model"] = ANALYZE_MODEL
    data["_input_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
    data["_output_tokens"] = getattr(usage, "completion_tokens", 0) or 0
    return attach_computed_scores(data)


def extract_people(transcript_text: str) -> tuple[str | None, str | None]:
    """Dedicated name extraction. Never returns status words as names."""
    from openai import OpenAI

    from shared.recording_meta import clean_person_name
    from shared.secrets import openai_api_key

    snippet = (transcript_text or "").strip()
    if not snippet:
        return None, None
    snippet = snippet[:12000]
    try:
        client = OpenAI(api_key=openai_api_key(), timeout=60)
        response = client.chat.completions.create(
            model=ANALYZE_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PEOPLE_SYSTEM},
                {"role": "user", "content": snippet},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        return (
            clean_person_name(str(data["customer_name"]) if data.get("customer_name") else None),
            clean_person_name(str(data["agent_name"]) if data.get("agent_name") else None),
        )
    except Exception:
        return None, None


def llm_cost_usd(input_tokens: int, output_tokens: int) -> float:
    # Conservative stand-in until 5.4-mini list prices are locked (4o-mini-like).
    return round(input_tokens * 0.15 / 1_000_000 + output_tokens * 0.60 / 1_000_000, 6)


def attach_computed_scores(data: dict[str, Any]) -> dict[str, Any]:
    from shared.qa_report import (
        COMPLIANCE_CHAPTERS,
        PROFESSIONALISM_CHAPTERS,
        QUALITY_CHAPTERS,
        combine_total,
        score_bucket,
    )

    scores = data.get("rubric_scores")
    if not isinstance(scores, dict):
        return data
    checklist = scores.get("checklist") or []
    if isinstance(checklist, list) and checklist:
        buckets = {"compliance": [], "professionalism": [], "quality": []}
        for item in checklist:
            if not isinstance(item, dict):
                continue
            chapter = item.get("chapter", item.get("section"))
            try:
                chapter_n = int(chapter)
            except (TypeError, ValueError):
                continue
            if chapter_n in COMPLIANCE_CHAPTERS:
                buckets["compliance"].append(item)
            elif chapter_n in PROFESSIONALISM_CHAPTERS:
                buckets["professionalism"].append(item)
            elif chapter_n in QUALITY_CHAPTERS:
                buckets["quality"].append(item)

        if any(buckets.values()):
            compliance = score_bucket(buckets["compliance"], 60)
            professionalism = score_bucket(buckets["professionalism"], 25)
            quality = score_bucket(buckets["quality"], 15)
            scores["compliance_60"] = compliance
            scores["professionalism_25"] = professionalism
            scores["quality_15"] = quality
            data["overall_score"] = combine_total(compliance, professionalism, quality)

    scores["compliance"] = scores.get("compliance_60")
    scores["professionalism"] = scores.get("professionalism_25")
    scores["quality"] = scores.get("quality_15")
    return data
