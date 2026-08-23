---
name: transcribe-hebrew
description: "Transcribe Hebrew call recordings with speaker diarization via shared STTProvider. Use before any call-qa scoring."
version: 0.1.0
author: Liba
license: MIT
metadata:
  hermes:
    tags: [call-qa, stt, hebrew, diarization]
    related_skills: [call-qa-rubric]
---

# Transcribe Hebrew calls

## Overview

Turn one audio file into a speaker-labeled Hebrew transcript. Use `shared.stt.STTProvider` only. Do not send raw audio to the LLM and do not call a vendor SDK from this skill ad-hoc.

Diarization is mandatory. Without speaker labels, call QA cannot tell agent from customer.

## When to Use

- A local audio path exists (manual run, or a file already fetched from Drive / Voice Center).
- Before loading `call-qa-rubric`.

Do not use for:

- English-only meetings
- Scoring or coaching text (that is the rubric skill)
- Inventing a second STT stack

## Procedure

1. Confirm the file exists and is audio (`wav`, `mp3`, `m4a`, `ogg`, `flac`).
2. Read `agents/call-qa/config.yaml` → `stt.provider`. If it is `null`, stop: Stage 2 has not chosen a vendor yet.
3. Call `shared.stt.get_stt_provider(name).transcribe(path, language="he")`.
4. Require at least two speaker labels in `Transcript.turns`. If only one speaker (or none), fail the job with `stt_status: missing_diarization`.
5. Keep timestamps when the provider returns them.
6. Record STT cost on the `CostReport` (per audio minute, vendor rate).
7. Pass the transcript text into the rubric skill. Do not skip diarization "to save money".

## Vendor

The concrete class is added in Stage 2 behind `STTProvider`. This skill does not name a fallback vendor and does not use Gemini/GPT native audio as transcription.

## Common Pitfalls

1. Dumping the mp3 into the chat model — forbidden. Quality and speaker split will be wrong, and it skips the STT interface.
2. Scoring before speakers are labeled.
3. Building a custom download/retry layer here — fetching files is `shared.sources`.
