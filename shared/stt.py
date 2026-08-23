"""STT interface. One vendor implementation is added in stage 2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TranscriptTurn:
    speaker: str
    text: str
    start_sec: float | None = None
    end_sec: float | None = None


@dataclass(frozen=True)
class Transcript:
    language: str
    turns: list[TranscriptTurn]
    provider: str
    duration_sec: float | None = None
    raw: dict = field(default_factory=dict)

    def as_text(self) -> str:
        lines = []
        for turn in self.turns:
            label = turn.speaker or "unknown"
            lines.append(f"[{label}] {turn.text}")
        return "\n".join(lines)


class STTProvider(ABC):
    """Hebrew + speaker diarization. Without diarization, QA cannot score who said what."""

    name: str

    @abstractmethod
    def transcribe(self, audio_path: Path, language: str = "he") -> Transcript:
        ...


def get_stt_provider(name: str | None) -> STTProvider:
    if not name:
        raise NotImplementedError("stt.provider is not set in agents/call-qa/config.yaml")
    key = name.strip().lower()
    if key in {"openai", "openai-gpt-4o-transcribe", "gpt-4o-transcribe"}:
        from shared.stt_openai import OpenAIAccurateSTT

        return OpenAIAccurateSTT()
    if key in {"openai-diarize", "openai-gpt-4o-transcribe-diarize", "gpt-4o-transcribe-diarize"}:
        from shared.stt_openai import OpenAIDiarizeSTT

        return OpenAIDiarizeSTT()
    raise NotImplementedError(f"STT provider {name!r} is not implemented yet.")
