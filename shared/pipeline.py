"""STT + rubric + OS report for one recording. Skip if OS already has status=done."""

from __future__ import annotations

from shared.analyze import analyze_transcript, extract_people, llm_cost_usd
from shared.logging import log
from shared.os_client import OsClient
from shared.recording_meta import (
    clean_person_name,
    display_name,
    drive_file_url,
    format_duration,
    guess_names_from_transcript,
    parse_call_datetime,
    parse_user_id,
)
from shared.sources import Recording, RecordingSource
from shared.stt import STTProvider
from shared.stt_openai import file_duration_sec, stt_cost_usd


def _call_fields(
    recording: Recording,
    duration_sec: float | None = None,
    customer_name: str | None = None,
    agent_name: str | None = None,
) -> dict:
    if duration_sec is not None:
        duration_sec = int(round(float(duration_sec)))
    customer_name = clean_person_name(customer_name)
    agent_name = clean_person_name(agent_name)
    file_name = recording.name
    call_date = parse_call_datetime(file_name) or recording.modified_time
    url = drive_file_url(recording.remote_id) if recording.source == "drive" else None
    metadata = {
        "file_name": file_name,
        "display_name": display_name(file_name, parse_call_datetime(file_name), customer_name),
        "drive_file_id": recording.remote_id,
        "drive_url": url,
        "user_id": parse_user_id(file_name),
        "duration_label": format_duration(duration_sec),
        "duration_sec": duration_sec,
        "call_date": call_date,
        "customer_name": customer_name,
        "agent_name": agent_name,
        "rep_name": agent_name,
    }
    if recording.size is not None:
        metadata["size_bytes"] = recording.size
    return {
        "duration_sec": duration_sec,
        "call_date": call_date,
        "audio_path": url or str(recording.path),
        "metadata": {k: v for k, v in metadata.items() if v is not None},
    }


def _people_from_analysis(analysis: dict) -> tuple[str | None, str | None]:
    ident = analysis.get("rubric_scores")
    ident = ident.get("identification") if isinstance(ident, dict) else None
    ident = ident if isinstance(ident, dict) else {}
    scores = analysis.get("rubric_scores") if isinstance(analysis.get("rubric_scores"), dict) else {}
    findings = analysis.get("findings")
    findings_ident = {}
    if isinstance(findings, dict):
        raw = findings.get("identification")
        if isinstance(raw, dict):
            findings_ident = raw
    customer = _soft(
        analysis.get("customer_name"),
        scores.get("customer_name"),
        ident.get("customer_name"),
        findings_ident.get("customer_name"),
        ident.get("שם_לקוח"),
        findings_ident.get("שם_לקוח"),
    )
    agent = _soft(
        analysis.get("agent_name"),
        scores.get("agent_name"),
        ident.get("agent_name"),
        ident.get("rep_name"),
        findings_ident.get("rep_name"),
        ident.get("שם_נציג"),
        findings_ident.get("שם_נציג"),
    )
    return clean_person_name(customer), clean_person_name(agent)


def _soft(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
            return value.strip()
    return None


def _inject_people(analysis: dict, customer: str | None, agent: str | None) -> None:
    if customer:
        analysis["customer_name"] = customer
    if agent:
        analysis["agent_name"] = agent
    scores = analysis.get("rubric_scores")
    if isinstance(scores, dict):
        if customer:
            scores["customer_name"] = customer
        if agent:
            scores["agent_name"] = agent
        ident = scores.get("identification")
        if not isinstance(ident, dict):
            ident = {}
            scores["identification"] = ident
        if customer:
            ident["customer_name"] = customer
        if agent:
            ident["rep_name"] = ident.get("rep_name") or agent
            ident["agent_name"] = ident.get("agent_name") or agent
    findings = analysis.get("findings")
    if isinstance(findings, dict):
        ident = findings.get("identification")
        if not isinstance(ident, dict):
            ident = {}
            findings["identification"] = ident
        if customer:
            ident["customer_name"] = customer
        if agent:
            ident["rep_name"] = ident.get("rep_name") or agent


def process_recording(
    os_client: OsClient,
    source: RecordingSource,
    stt: STTProvider,
    recording: Recording,
    run_id: str,
    language: str = "auto",
    force: bool = False,
) -> str:
    """Return skipped | ok | failed."""
    external_id = recording.remote_id or recording.name or str(recording.path)
    fields = _call_fields(recording)
    log("register", external_id=external_id, name=recording.name, display_name=fields["metadata"].get("display_name"))
    registered = os_client.register_call(
        external_id=external_id,
        source=recording.source,
        duration_sec=fields["duration_sec"],
        call_date=fields["call_date"],
        audio_path=fields["audio_path"],
        metadata=fields["metadata"],
    )
    call_id = str(registered.get("id") or registered.get("call_id") or "")
    skip = bool(registered.get("skip_analysis")) or (
        registered.get("created") is False and registered.get("status") == "done"
    )
    if skip and not force:
        log("skip_done", call_id=call_id, external_id=external_id, reason=registered.get("reason"))
        return "skipped"
    if not call_id:
        log("failed", external_id=external_id, error="register returned no call id")
        return "failed"

    try:
        os_client.set_call_status(call_id, "processing")
        os_client.log(run_id, "info", f"processing {recording.name or external_id}")
        path = source.fetch(recording)
        transcript = stt.transcribe(path, language=language)
        text = transcript.as_text()
        guessed_customer, guessed_agent = guess_names_from_transcript(text)
        extracted_customer, extracted_agent = extract_people(text)
        duration = transcript.duration_sec or file_duration_sec(path)
        later = _call_fields(
            recording,
            duration,
            customer_name=extracted_customer or guessed_customer,
            agent_name=extracted_agent or guessed_agent,
        )
        os_client.register_call(
            external_id=external_id,
            source=recording.source,
            duration_sec=later["duration_sec"],
            call_date=later["call_date"],
            audio_path=later["audio_path"],
            metadata=later["metadata"],
        )
        stt_usd = stt_cost_usd(duration)
        segments = [
            {
                "speaker": turn.speaker,
                "text": turn.text,
                "start_sec": turn.start_sec,
                "end_sec": turn.end_sec,
            }
            for turn in transcript.turns
        ]
        os_client.save_transcript(
            call_id,
            text=text,
            segments=segments,
            provider=transcript.provider,
            cost_usd=stt_usd,
            language=transcript.language,
        )
        os_client.report_cost(
            run_id,
            "stt",
            stt_usd,
            units=(duration or 0) / 60.0,
            unit_type="minutes",
        )

        analysis = analyze_transcript(text)
        input_tokens = int(analysis.pop("_input_tokens", 0) or 0)
        output_tokens = int(analysis.pop("_output_tokens", 0) or 0)
        model = str(analysis.pop("_model", "") or "gpt-5.4-mini")
        llm_usd = llm_cost_usd(input_tokens, output_tokens)
        from_analysis_c, from_analysis_a = _people_from_analysis(analysis)
        customer_name = extracted_customer or from_analysis_c or guessed_customer
        agent_name = extracted_agent or from_analysis_a or guessed_agent
        customer_name = clean_person_name(customer_name)
        agent_name = clean_person_name(agent_name)
        _inject_people(analysis, customer_name, agent_name)
        os_client.save_analysis(
            call_id,
            run_id=run_id,
            summary=analysis.get("summary"),
            overall_score=analysis.get("overall_score"),
            rubric_scores=analysis.get("rubric_scores"),
            findings=analysis.get("findings"),
            recommendations=analysis.get("recommendations"),
            model=model,
        )
        named = _call_fields(
            recording,
            duration,
            customer_name=customer_name,
            agent_name=agent_name,
        )
        os_client.register_call(
            external_id=external_id,
            source=recording.source,
            duration_sec=named["duration_sec"],
            call_date=named["call_date"],
            audio_path=named["audio_path"],
            metadata=named["metadata"],
        )
        os_client.report_cost(
            run_id,
            "llm",
            llm_usd,
            units=float(input_tokens + output_tokens),
            unit_type="tokens",
        )
        os_client.set_call_status(call_id, "done")
        log(
            "done",
            call_id=call_id,
            external_id=external_id,
            overall_score=analysis.get("overall_score"),
            customer_name=customer_name,
            agent_name=agent_name,
            stt_usd=stt_usd,
            llm_usd=llm_usd,
        )
        return "ok"
    except Exception as exc:
        log("failed", call_id=call_id, external_id=external_id, error=str(exc))
        try:
            os_client.set_call_status(call_id, "failed")
            os_client.log(run_id, "error", f"{external_id}: {exc}")
        except Exception:
            pass
        return "failed"
