"""Thematic analysis of interview studies (LLM-assisted open coding).

Runs as a background job (jobs.start_job) since it makes one LLM call per
session plus codebook generation and report synthesis:

1. Inductive open coding: generate a codebook grounded in the research question.
2. Code every transcript into verbatim coded segments.
3. Synthesize a markdown thematic report.

Codebook -> `codebooks`, segments -> `coded_segments`, report + code
frequencies -> an `analyses` row (kind="thematic").
"""
from collections import Counter

from ... import db, llm
from ...config import DEFAULT_PIPELINE_MODEL
from ...jobs import start_job, update_progress

CODEBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "codes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string",
                           "description": "short lowercase slug, e.g. 'work_pressure'"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "parent_id": {"type": ["string", "null"],
                                  "description": "id of the parent code, or null for top-level"},
                },
                "required": ["id", "name", "description", "parent_id"],
            },
        },
    },
    "required": ["codes"],
}

SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code_id": {"type": "string"},
                    "text": {"type": "string",
                             "description": "verbatim quote from the transcript"},
                    "memo": {"type": "string",
                             "description": "short analytic memo on why this code applies"},
                },
                "required": ["code_id", "text", "memo"],
            },
        },
    },
    "required": ["segments"],
}


def _transcript(session):
    """Render a session's messages as a plain-text interview transcript."""
    lines = []
    for m in session.get("messages") or []:
        speaker = "Interviewer" if m.get("role") == "assistant" else "Respondent"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def _codable_sessions(study_id):
    """Completed sessions, plus active ones that already contain respondent input."""
    sessions = db.query("sessions", "study_id = ?", (study_id,), order="started_at ASC")
    out = []
    for s in sessions:
        messages = s.get("messages") or []
        has_content = any(m.get("role") == "user" and (m.get("content") or "").strip()
                          for m in messages)
        if s.get("status") == "completed" or (s.get("status") == "active" and has_content):
            if has_content:
                out.append(s)
    return out


def start_thematic_job(study_id):
    """Kick off the thematic-analysis background job; returns the job id."""
    return start_job(
        "thematic",
        lambda job_id: run_thematic_analysis(study_id, job_id),
        ref_table="studies", ref_id=study_id,
    )


def run_thematic_analysis(study_id, job_id):
    study = db.get("studies", study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")
    rq = study.get("research_question", "") or "(not specified)"

    sessions = _codable_sessions(study_id)
    if not sessions:
        raise ValueError("No interview sessions with content to analyze yet.")
    transcripts = [(s, _transcript(s)) for s in sessions]

    # Step 1 — inductive open coding: generate the codebook over all material.
    update_progress(job_id, 0.05, "Generating codebook from transcripts")
    corpus = "\n\n".join(
        f"--- Interview {i + 1} ({s.get('respondent_name') or 'anonymous'}) ---\n{t}"
        for i, (s, t) in enumerate(transcripts)
    )
    codebook_system = (
        "You are a qualitative researcher performing inductive open coding "
        "(thematic analysis, Braun & Clarke). Derive codes from the data itself, "
        "grounded in the research question. Produce 8-20 codes; use parent_id to "
        "group sub-codes under broader codes where a hierarchy emerges. Code ids "
        "are short lowercase slugs."
    )
    codebook_prompt = (
        f"Research question: {rq}\n\n"
        f"Interview transcripts:\n\n{corpus}\n\n"
        "Generate the codebook."
    )
    raw = llm.complete_json(codebook_system, codebook_prompt, CODEBOOK_SCHEMA,
                            model=DEFAULT_PIPELINE_MODEL)
    codes, seen = [], set()
    for c in raw.get("codes", []):
        cid = (c.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        codes.append({
            "id": cid,
            "name": c.get("name", cid),
            "description": c.get("description", ""),
            "parent_id": c.get("parent_id") or None,
        })
    if not codes:
        raise ValueError("Codebook generation returned no codes.")
    for c in codes:  # drop dangling parents
        if c["parent_id"] not in seen:
            c["parent_id"] = None
    codebook_id = db.insert("codebooks", {
        "study_id": study_id,
        "name": f"Thematic codebook — {study.get('title', study_id)}",
        "codes": codes,
    })
    code_names = {c["id"]: c["name"] for c in codes}

    # Step 2 — code each session's transcript into segments.
    counts = Counter()
    n_segments = 0
    codebook_text = "\n".join(
        f"- {c['id']}: {c['name']} — {c['description']}"
        + (f" (sub-code of {c['parent_id']})" if c["parent_id"] else "")
        for c in codes
    )
    coding_system = (
        "You are a qualitative researcher applying an existing codebook to an "
        "interview transcript. Extract every passage that fits a code as a "
        "verbatim quote (respondent speech, copied exactly), assign exactly one "
        "code_id from the codebook, and add a short analytic memo. Only use "
        "code_ids that appear in the codebook."
    )
    for i, (session, transcript) in enumerate(transcripts):
        update_progress(job_id, 0.10 + 0.70 * i / len(transcripts),
                        f"Coding session {i + 1} of {len(transcripts)}")
        coding_prompt = (
            f"Research question: {rq}\n\nCodebook:\n{codebook_text}\n\n"
            f"Transcript:\n{transcript}\n\nCode this transcript."
        )
        coded = llm.complete_json(coding_system, coding_prompt, SEGMENTS_SCHEMA,
                                  model=DEFAULT_PIPELINE_MODEL)
        for seg in coded.get("segments", []):
            code_id = (seg.get("code_id") or "").strip()
            text = (seg.get("text") or "").strip()
            if code_id not in code_names or not text:
                continue
            db.insert("coded_segments", {
                "codebook_id": codebook_id,
                "session_id": session["id"],
                "code_id": code_id,
                "text": text,
                "memo": (seg.get("memo") or "").strip(),
            })
            counts[code_id] += 1
            n_segments += 1

    code_counts = [
        {"code_id": cid, "name": code_names[cid], "count": counts.get(cid, 0)}
        for cid in sorted(code_names, key=lambda c: -counts.get(c, 0))
    ]

    # Step 3 — synthesize the thematic report.
    update_progress(job_id, 0.85, "Synthesizing thematic report")
    segments = db.query("coded_segments", "codebook_id = ?", (codebook_id,),
                        order="created_at ASC")
    quotes_text = "\n".join(
        f"[{s['code_id']}] \"{s['text']}\" (memo: {s['memo']})" for s in segments
    )
    counts_text = "\n".join(f"- {c['name']} ({c['code_id']}): {c['count']} segments"
                            for c in code_counts)
    report_system = (
        "You are a qualitative researcher writing up a thematic analysis. "
        "Write a markdown report with: a short methods note; the themes, each "
        "with a definition, supporting verbatim quotes (blockquotes), and code "
        "frequencies; and a concluding section that answers the research "
        "question based on the themes. Be faithful to the data — quote only "
        "what is given."
    )
    report_prompt = (
        f"Study: {study.get('title', '')}\nResearch question: {rq}\n"
        f"Number of interviews: {len(transcripts)}\n\n"
        f"Codebook:\n{codebook_text}\n\nCode frequencies:\n{counts_text}\n\n"
        f"Coded segments:\n{quotes_text}\n\nWrite the thematic report."
    )
    report_md = llm.complete(report_system, report_prompt,
                             model=DEFAULT_PIPELINE_MODEL)

    update_progress(job_id, 0.95, "Saving results")
    analysis_id = db.insert("analyses", {
        "kind": "thematic",
        "study_id": study_id,
        "project_id": study.get("project_id"),
        "title": f"Thematic analysis — {study.get('title', study_id)}",
        "params": {"n_sessions": len(transcripts)},
        "results": {
            "report_md": report_md,
            "code_counts": code_counts,
            "codebook_id": codebook_id,
            "n_sessions": len(transcripts),
            "n_segments": n_segments,
        },
        "status": "done",
        "completed_at": db.now(),
    })
    return {"analysis_id": analysis_id, "codebook_id": codebook_id,
            "n_segments": n_segments}
