"""OpenAI transcription. Default = accurate words, then speaker tags without rewriting."""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.stt import STTProvider, Transcript, TranscriptTurn

MAX_BYTES = 25 * 1024 * 1024
USD_PER_MINUTE = 0.006
ANALYZE_MODEL = "gpt-5.4-mini"

STT_PROMPT = (
    "שיחת מכירה של סוכנות ביטוח ליבה. עברית ורוסית באותה שיחה. "
    "מונחים: הר הביטוח, גילוי נאות, הצהרת בריאות, מחלות קשות, ביטוח חיים, "
    "ריסק, כפל ביטוחי, חיתום, פרמיה, פוליסה, מוטבים, יורשים חוקיים, "
    "מגדל, מנורה, כלל, הראל, הפניקס, הכשרה, איילון, מקבי שירות, "
    "ניתוח בריאטרי, אנמיה, אינפוזיה."
)


class OpenAIAccurateSTT(STTProvider):
    """gpt-4o-transcribe for wording, then GPT only tags סוכן/לקוח."""

    name = "openai-gpt-4o-transcribe"

    def transcribe(self, audio_path: Path, language: str = "auto") -> Transcript:
        from openai import OpenAI

        from shared.secrets import openai_api_key

        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        size = audio_path.stat().st_size
        if size > MAX_BYTES:
            raise ValueError(
                f"Audio is {size} bytes; OpenAI transcription max is {MAX_BYTES}. "
                "Compress to mp3 under 25MB."
            )

        client = OpenAI(api_key=openai_api_key(), timeout=30 * 60)
        upload = ("call.mp3", audio_path.read_bytes(), "audio/mpeg")
        lang = _api_language(language)
        attempts: list[dict] = [
            {"model": "gpt-4o-transcribe", "response_format": "json", "prompt": STT_PROMPT},
            {"model": "gpt-4o-transcribe", "response_format": "json"},
            {"model": "whisper-1", "response_format": "verbose_json"},
        ]
        payload: dict = {}
        unlabeled: list[TranscriptTurn] = []
        last_error: Exception | None = None
        used_model = "gpt-4o-transcribe"
        for kwargs in attempts:
            if lang and kwargs["model"] != "whisper-1":
                kwargs = {**kwargs, "language": lang}
            try:
                result = client.audio.transcriptions.create(file=upload, **kwargs)
            except Exception as exc:
                last_error = exc
                continue
            payload = _result_payload(result)
            unlabeled = _turns_from_payload(payload)
            used_model = str(kwargs["model"])
            text_len = sum(len(t.text) for t in unlabeled)
            print(f"stt {used_model}: {text_len} chars from {size} byte file")
            if _enough_text(unlabeled, size):
                break
        else:
            if not unlabeled:
                raise RuntimeError(
                    f"STT returned empty transcript for {audio_path.name}"
                    + (f" ({last_error})" if last_error else "")
                )

        if not unlabeled:
            raise RuntimeError(f"STT returned empty transcript for {audio_path.name}")
        if not _enough_text(unlabeled, size):
            raise RuntimeError(
                f"STT returned too little text ({sum(len(t.text) for t in unlabeled)} chars) "
                f"for {size} byte file"
            )

        turns = _tag_speakers(client, unlabeled)
        duration = _duration_from_payload(payload) or file_duration_sec(audio_path)
        return Transcript(
            language=language,
            turns=turns,
            provider=f"openai-{used_model}",
            duration_sec=float(duration) if duration is not None else None,
            raw=payload,
        )


class OpenAIDiarizeSTT(STTProvider):
    name = "openai-gpt-4o-transcribe-diarize"

    def transcribe(self, audio_path: Path, language: str = "auto") -> Transcript:
        from openai import OpenAI

        from shared.secrets import openai_api_key

        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        size = audio_path.stat().st_size
        if size > MAX_BYTES:
            raise ValueError(
                f"Audio is {size} bytes; OpenAI transcription max is {MAX_BYTES}. "
                "Compress to mp3 under 25MB."
            )

        client = OpenAI(api_key=openai_api_key(), timeout=30 * 60)
        kwargs: dict = {
            "model": "gpt-4o-transcribe-diarize",
            "response_format": "diarized_json",
            "chunking_strategy": "auto",
            "prompt": STT_PROMPT,
        }
        lang = _api_language(language)
        if lang:
            kwargs["language"] = lang
        try:
            with audio_path.open("rb") as fh:
                result = client.audio.transcriptions.create(file=fh, **kwargs)
        except Exception:
            kwargs.pop("prompt", None)
            kwargs.pop("language", None)
            with audio_path.open("rb") as fh:
                result = client.audio.transcriptions.create(file=fh, **kwargs)

        payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        segments = payload.get("segments") or []
        turns: list[TranscriptTurn] = []
        for seg in segments:
            turns.append(
                TranscriptTurn(
                    speaker=str(seg.get("speaker") or "unknown"),
                    text=str(seg.get("text") or "").strip(),
                    start_sec=_num(seg.get("start")),
                    end_sec=_num(seg.get("end")),
                )
            )
        if not turns and payload.get("text"):
            turns = [TranscriptTurn(speaker="unknown", text=str(payload["text"]).strip())]

        duration = _duration_from_payload(payload) or file_duration_sec(audio_path)

        return Transcript(
            language=language,
            turns=turns,
            provider=self.name,
            duration_sec=float(duration) if duration is not None else None,
            raw=payload,
        )


def _tag_speakers(client, turns: list[TranscriptTurn]) -> list[TranscriptTurn]:
    if not turns:
        return turns
    numbered = "\n".join(f"{i}\t{t.text}" for i, t in enumerate(turns))
    try:
        response = client.chat.completions.create(
            model=ANALYZE_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            timeout=180,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "תייג כל שורה כסוכן או לקוח. אסור לשנות טקסט. "
                        "החזר JSON: {\"labels\": [\"סוכן\" או \"לקוח\", ...]} "
                        "באורך זהה למספר השורות."
                    ),
                },
                {"role": "user", "content": numbered},
            ],
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        labels = data.get("labels") or data.get("speakers") or []
        if len(labels) != len(turns):
            return turns
        tagged = []
        for turn, label in zip(turns, labels):
            speaker = str(label).strip()
            if speaker not in {"סוכן", "לקוח"}:
                speaker = turn.speaker
            tagged.append(
                TranscriptTurn(
                    speaker=speaker,
                    text=turn.text,
                    start_sec=turn.start_sec,
                    end_sec=turn.end_sec,
                )
            )
        return tagged
    except Exception:
        return turns


def _turns_from_payload(payload: dict) -> list[TranscriptTurn]:
    raw_segments = payload.get("segments") or []
    unlabeled: list[TranscriptTurn] = []
    for seg in raw_segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        unlabeled.append(
            TranscriptTurn(
                speaker="unknown",
                text=text,
                start_sec=_num(seg.get("start")),
                end_sec=_num(seg.get("end")),
            )
        )
    if unlabeled:
        return unlabeled
    text = str(payload.get("text") or "").strip()
    if not text:
        return []
    chunks = [
        chunk.strip()
        for chunk in re.split(r"(?<=[.!?…;:])\s+|\n+", text)
        if chunk.strip()
    ]
    if not chunks:
        chunks = [text]
    return [TranscriptTurn(speaker="unknown", text=chunk) for chunk in chunks]


def _result_payload(result: object) -> dict:
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = dict(result)
    elif isinstance(result, str):
        payload = {"text": result}
    else:
        payload = {}
    text = getattr(result, "text", None)
    if text and not payload.get("text"):
        payload["text"] = text
    return payload


def _duration_from_payload(payload: dict) -> float | None:
    duration = _num(payload.get("duration"))
    if duration is not None:
        return duration
    usage = payload.get("usage") or {}
    if isinstance(usage, dict):
        return _num(usage.get("seconds"))
    return _num(getattr(usage, "seconds", None))


def _enough_text(turns: list[TranscriptTurn], size: int) -> bool:
    n = sum(len(t.text) for t in turns)
    if size >= 1_000_000:
        return n >= 800
    if size >= 200_000:
        return n >= 200
    return n >= 20


def file_duration_sec(path: Path) -> float | None:
    """Best-effort duration from an MP3 frame header (CBR voice recordings)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 4:
        return None
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        i = 10 + (data[6] << 21 | data[7] << 14 | data[8] << 7 | data[9])
    while i + 4 <= len(data) and not (data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0):
        i += 1
    if i + 4 > len(data):
        return None
    header = int.from_bytes(data[i : i + 4], "big")
    version_id = (header >> 19) & 3
    layer_id = (header >> 17) & 3
    bitrate_idx = (header >> 12) & 15
    mpeg1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    mpeg2_l3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
    table = mpeg1_l3 if version_id == 3 else mpeg2_l3
    if layer_id != 1:
        table = mpeg2_l3 if version_id != 3 else mpeg1_l3
    kbps = table[bitrate_idx] if 0 <= bitrate_idx < len(table) else 0
    if kbps <= 40:
        kbps = 43
    return round((len(data) - i) * 8 / (kbps * 1000), 3)


def stt_cost_usd(duration_sec: float | None) -> float:
    minutes = (duration_sec or 0) / 60.0
    return round(minutes * USD_PER_MINUTE, 6)


def _api_language(language: str | None) -> str | None:
    if not language:
        return None
    key = language.strip().lower()
    if key in {"", "auto", "none", "multilingual", "null"}:
        return None
    return key


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
