"""Qualitative analysis of interview studies (LLM-assisted coding).

Background jobs (one LLM call per session plus report synthesis):

- thematic   — inductive open coding: generate a codebook, code every
               transcript, synthesize a report.
- deductive  — code with a FIXED pre-existing codebook (no inductive step);
               segments land under the existing codebook_id.
- intercoder — second-coder simulation: re-code independently ("Coder B"),
               compute Cohen's κ per code on the session × code presence
               matrix. Coder B assignments are a reliability probe and are
               NEVER inserted into coded_segments.

Synchronous (pure Python, no LLM) kinds live in SYNC_KINDS:

- cooccurrence — session-level code co-occurrence matrix for one codebook.

Codebook -> `codebooks`, segments -> `coded_segments`, report + numbers ->
an `analyses` row (kind="thematic"|"deductive"|"intercoder").
"""
import csv
import io
import json
import re
from collections import Counter

from ... import db, llm
from ...config import DEFAULT_PIPELINE_MODEL
from ...jobs import job as job_task, start_job, update_progress

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


CODING_SYSTEM = (
    "You are a qualitative researcher applying an existing codebook to an "
    "interview transcript. Extract every passage that fits a code as a "
    "verbatim quote (respondent speech, copied exactly), assign exactly one "
    "code_id from the codebook, and add a short analytic memo. Only use "
    "code_ids that appear in the codebook."
)

CODER_B_SYSTEM = (
    "You are Coder B, a second independent qualitative coder performing an "
    "intercoder-reliability check. You have NOT seen any prior coding of this "
    "material and must form your own judgment from the transcript and the "
    "codebook alone. Read the transcript carefully and extract every passage "
    "that fits a code as a verbatim quote (respondent speech, copied exactly), "
    "assigning exactly one code_id from the codebook per segment, with a short "
    "analytic memo. Apply a code only when the transcript clearly supports it. "
    "Only use code_ids that appear in the codebook."
)


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return s or "code"


def _codebook_text(codes):
    """Render a codes list as the plain-text codebook given to the coder."""
    return "\n".join(
        f"- {c['id']}: {c.get('name', c['id'])} — {c.get('description', '')}"
        + (f" (sub-code of {c['parent_id']})" if c.get("parent_id") else "")
        for c in codes
    )


def _code_session(research_question, codebook_text, transcript, valid_code_ids,
                  system=CODING_SYSTEM):
    """One LLM coding pass over one transcript with a fixed codebook.

    Returns [{"code_id", "text", "memo"}]; segments with unknown code_ids or
    empty text are dropped.
    """
    coding_prompt = (
        f"Research question: {research_question}\n\nCodebook:\n{codebook_text}\n\n"
        f"Transcript:\n{transcript}\n\nCode this transcript."
    )
    coded = llm.complete_json(system, coding_prompt, SEGMENTS_SCHEMA,
                              model=DEFAULT_PIPELINE_MODEL)
    out = []
    for seg in coded.get("segments", []):
        code_id = (seg.get("code_id") or "").strip()
        text = (seg.get("text") or "").strip()
        if code_id not in valid_code_ids or not text:
            continue
        out.append({"code_id": code_id, "text": text,
                    "memo": (seg.get("memo") or "").strip()})
    return out


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
        {"study_id": study_id},
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
    codebook_text = _codebook_text(codes)
    for i, (session, transcript) in enumerate(transcripts):
        update_progress(job_id, 0.10 + 0.70 * i / len(transcripts),
                        f"Coding session {i + 1} of {len(transcripts)}")
        for seg in _code_session(rq, codebook_text, transcript, set(code_names)):
            db.insert("coded_segments", {
                "codebook_id": codebook_id,
                "session_id": session["id"],
                **seg,
            })
            counts[seg["code_id"]] += 1
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


@job_task("thematic")
def _thematic_job(job_id, study_id=None):
    return run_thematic_analysis(study_id, job_id)


# --------------------------------------------------------------------------
# Deductive coding — apply a FIXED, pre-existing codebook (no inductive step).
# --------------------------------------------------------------------------

def parse_codebook_upload(text):
    """Parse an uploaded codebook into `[{"id","name","description","parent_id"}]`.

    Accepts either JSON (a list of code objects, or `{"codes": [...]}`) or CSV
    with a header row using the columns id / name / description / parent_id
    (only `name` is required; a missing id is slugified from the name).
    Duplicate ids are deduplicated with a numeric suffix; parent_ids that don't
    resolve to a code are dropped. Raises ValueError on malformed input.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Codebook upload is empty.")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"Codebook JSON could not be parsed: {e}")
        if isinstance(data, dict) and isinstance(data.get("codes"), list):
            data = data["codes"]
        if not isinstance(data, list):
            raise ValueError(
                'Codebook JSON must be a list of code objects (or {"codes": [...]}).')
        rows = data
    else:
        reader = csv.DictReader(io.StringIO(text))
        fields = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
        if "name" not in fields:
            raise ValueError(
                "Codebook CSV needs a header row with at least a 'name' column "
                "(optional columns: id, description, parent_id).")
        rows = [{(k or "").strip().lower(): v for k, v in r.items() if k}
                for r in reader]

    codes, seen = [], set()
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ValueError(f"Codebook entry {i} is not an object with code fields.")
        name = str(r.get("name") or "").strip()
        if not name:
            raise ValueError(f"Codebook entry {i} has an empty name.")
        cid = str(r.get("id") or "").strip() or _slug(name)
        base, n = cid, 2
        while cid in seen:
            cid, n = f"{base}_{n}", n + 1
        seen.add(cid)
        codes.append({
            "id": cid,
            "name": name,
            "description": str(r.get("description") or "").strip(),
            "parent_id": str(r.get("parent_id") or "").strip() or None,
        })
    if not codes:
        raise ValueError("Codebook upload contains no codes.")
    for c in codes:  # drop dangling parents
        if c["parent_id"] is not None and c["parent_id"] not in seen:
            c["parent_id"] = None
    return codes


def start_deductive_job(study_id, codebook_id):
    """Kick off deductive coding with an existing codebook; returns the job id."""
    return start_job(
        "deductive",
        {"study_id": study_id, "codebook_id": codebook_id},
        ref_table="studies", ref_id=study_id,
    )


def run_deductive_analysis(study_id, codebook_id, job_id):
    study = db.get("studies", study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")
    codebook = db.get("codebooks", codebook_id)
    if not codebook:
        raise ValueError(f"Codebook not found: {codebook_id}")
    codes = codebook.get("codes") or []
    if not codes:
        raise ValueError("The selected codebook contains no codes.")
    rq = study.get("research_question", "") or "(not specified)"

    sessions = _codable_sessions(study_id)
    if not sessions:
        raise ValueError("No interview sessions with content to analyze yet.")
    transcripts = [(s, _transcript(s)) for s in sessions]

    code_names = {c["id"]: c.get("name", c["id"]) for c in codes}
    codebook_text = _codebook_text(codes)

    # Code each session against EXACTLY the fixed codebook.
    counts = Counter()
    n_segments = 0
    for i, (session, transcript) in enumerate(transcripts):
        update_progress(job_id, 0.05 + 0.75 * i / len(transcripts),
                        f"Coding session {i + 1} of {len(transcripts)} (fixed codebook)")
        for seg in _code_session(rq, codebook_text, transcript, set(code_names)):
            db.insert("coded_segments", {
                "codebook_id": codebook_id,
                "session_id": session["id"],
                **seg,
            })
            counts[seg["code_id"]] += 1
            n_segments += 1

    code_counts = [
        {"code_id": cid, "name": code_names[cid], "count": counts.get(cid, 0)}
        for cid in sorted(code_names, key=lambda c: -counts.get(c, 0))
    ]

    # Synthesize the deductive coding report.
    update_progress(job_id, 0.85, "Synthesizing deductive coding report")
    segments = db.query("coded_segments", "codebook_id = ?", (codebook_id,),
                        order="created_at ASC")
    quotes_text = "\n".join(
        f"[{s['code_id']}] \"{s['text']}\" (memo: {s['memo']})" for s in segments
    )
    counts_text = "\n".join(f"- {c['name']} ({c['code_id']}): {c['count']} segments"
                            for c in code_counts)
    report_system = (
        "You are a qualitative researcher writing up a DEDUCTIVE coding "
        "analysis: the data was coded against a fixed, pre-existing codebook "
        "(no inductive codebook development). Write a markdown report with: a "
        "short methods note naming the deductive approach; per-code sections "
        "with code frequencies and exemplar verbatim quotes (blockquotes); a "
        "critical section on what this fixed analytic lens captured well and "
        "what it did NOT capture — explicitly note any salient content in the "
        "transcripts that the codebook could not categorize (codes with zero "
        "segments are also informative); and a concluding section answering "
        "the research question within the limits of the codebook. Be faithful "
        "to the data — quote only what is given."
    )
    report_prompt = (
        f"Study: {study.get('title', '')}\nResearch question: {rq}\n"
        f"Number of interviews: {len(transcripts)}\n"
        f"Fixed codebook ({codebook.get('name', '')}):\n{codebook_text}\n\n"
        f"Code frequencies:\n{counts_text}\n\n"
        f"Coded segments:\n{quotes_text}\n\nWrite the deductive coding report."
    )
    report_md = llm.complete(report_system, report_prompt,
                             model=DEFAULT_PIPELINE_MODEL)

    update_progress(job_id, 0.95, "Saving results")
    analysis_id = db.insert("analyses", {
        "kind": "deductive",
        "study_id": study_id,
        "project_id": study.get("project_id"),
        "title": f"Deductive coding — {study.get('title', study_id)}",
        "params": {"codebook_id": codebook_id, "n_sessions": len(transcripts)},
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


@job_task("deductive")
def _deductive_job(job_id, study_id=None, codebook_id=None):
    return run_deductive_analysis(study_id, codebook_id, job_id)


# --------------------------------------------------------------------------
# Intercoder agreement — second-coder simulation + Cohen's κ.
# --------------------------------------------------------------------------

def cohens_kappa(a_labels, b_labels):
    """Cohen's κ for two equal-length binary label lists (0/1 or truthy).

    Returns None when κ is undefined (empty input, or both coders assign the
    same single category to every unit so expected agreement is 1).
    """
    if len(a_labels) != len(b_labels):
        raise ValueError("Label lists must have equal length.")
    n = len(a_labels)
    if n == 0:
        return None
    a = [1 if x else 0 for x in a_labels]
    b = [1 if x else 0 for x in b_labels]
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:  # degenerate: chance agreement saturates -> κ undefined
        return None
    return (po - pe) / (1 - pe)


# Landis & Koch (1977) interpretation bands, given to the report writer.
_KAPPA_BANDS = (
    "Landis & Koch (1977) interpretation: < 0 poor; 0.00-0.20 slight; "
    "0.21-0.40 fair; 0.41-0.60 moderate; 0.61-0.80 substantial; "
    "0.81-1.00 almost perfect."
)


def start_intercoder_job(study_id, analysis_id):
    """Kick off the intercoder-agreement job for a completed thematic or
    deductive analysis; returns the job id."""
    return start_job(
        "intercoder",
        {"study_id": study_id, "analysis_id": analysis_id},
        ref_table="studies", ref_id=study_id,
    )


def run_intercoder_analysis(study_id, analysis_id, job_id):
    study = db.get("studies", study_id)
    if not study:
        raise ValueError(f"Study not found: {study_id}")
    base = db.get("analyses", analysis_id)
    if not base:
        raise ValueError(f"Analysis not found: {analysis_id}")
    if base.get("kind") not in ("thematic", "deductive"):
        raise ValueError(
            "Intercoder agreement needs a thematic or deductive analysis as its base.")
    codebook_id = (base.get("results") or {}).get("codebook_id")
    if not codebook_id:
        raise ValueError("The base analysis carries no codebook_id in its results.")
    codebook = db.get("codebooks", codebook_id)
    if not codebook:
        raise ValueError(f"Codebook not found: {codebook_id}")
    codes = codebook.get("codes") or []
    if not codes:
        raise ValueError("The base analysis' codebook contains no codes.")
    rq = study.get("research_question", "") or "(not specified)"

    sessions = _codable_sessions(study_id)
    if not sessions:
        raise ValueError("No interview sessions with content to analyze yet.")
    session_ids = [s["id"] for s in sessions]

    # Coder A = the existing coded_segments of this codebook (presence level).
    a_presence = {sid: set() for sid in session_ids}
    for seg in db.query("coded_segments", "codebook_id = ?", (codebook_id,)):
        if seg["session_id"] in a_presence:
            a_presence[seg["session_id"]].add(seg["code_id"])

    # Coder B = an independent re-coding pass. These assignments are a
    # reliability probe only — they are NEVER inserted into coded_segments.
    code_names = {c["id"]: c.get("name", c["id"]) for c in codes}
    codebook_text = _codebook_text(codes)
    b_presence = {}
    for i, session in enumerate(sessions):
        update_progress(job_id, 0.05 + 0.75 * i / len(sessions),
                        f"Coder B re-coding session {i + 1} of {len(sessions)}")
        segs = _code_session(rq, codebook_text, _transcript(session),
                             set(code_names), system=CODER_B_SYSTEM)
        b_presence[session["id"]] = {s["code_id"] for s in segs}

    # Agreement on the session × code binary presence matrix. All κ numbers
    # are computed here in Python — the LLM only writes prose around them.
    n = len(session_ids)
    kappa_per_code = []
    for c in codes:
        cid = c["id"]
        a_labels = [1 if cid in a_presence[sid] else 0 for sid in session_ids]
        b_labels = [1 if cid in b_presence[sid] else 0 for sid in session_ids]
        agree = sum(1 for x, y in zip(a_labels, b_labels) if x == y)
        kappa_per_code.append({
            "code_id": cid,
            "name": code_names[cid],
            "kappa": cohens_kappa(a_labels, b_labels),
            "percent_agreement": agree / n,
            "n": n,
        })
    defined = [k["kappa"] for k in kappa_per_code if k["kappa"] is not None]
    kappa_mean = sum(defined) / len(defined) if defined else None
    mean_pct = sum(k["percent_agreement"] for k in kappa_per_code) / len(kappa_per_code)

    # Report — written by the LLM strictly from the numbers computed above.
    update_progress(job_id, 0.85, "Writing intercoder agreement report")
    table_text = "\n".join(
        f"- {k['name']} ({k['code_id']}): κ = "
        + (f"{k['kappa']:.3f}" if k["kappa"] is not None
           else "undefined (both coders constant — no variation to agree beyond chance)")
        + f", percent agreement = {k['percent_agreement']:.0%}, n = {k['n']} sessions"
        for k in kappa_per_code
    )
    report_system = (
        "You are a methodologist writing up an intercoder-reliability check "
        "for an AI-assisted qualitative coding analysis. Coder A is the "
        "original coding; Coder B is a deliberately independent second AI "
        "coder that saw only the transcripts and the codebook. Write a "
        "markdown report that: (1) explains the procedure; (2) states clearly "
        "that UNITIZATION IS AT THE SESSION × CODE PRESENCE LEVEL — AI coders "
        "segment text differently, so quote-level alignment is not meaningful "
        "and agreement means 'both coders found this code somewhere in this "
        "session'; (3) presents the per-code κ and percent-agreement table "
        "EXACTLY as given (do not recompute or alter any number); (4) "
        "interprets the values using the Landis & Koch bands provided, "
        "including which codes are reliable and which need codebook "
        "refinement; (5) explains why κ is undefined where marked. "
        "Do not invent numbers."
    )
    report_prompt = (
        f"Study: {study.get('title', '')}\nResearch question: {rq}\n"
        f"Base analysis: {base.get('title', '')} (kind: {base.get('kind')})\n"
        f"Codebook ({codebook.get('name', '')}):\n{codebook_text}\n\n"
        f"Sessions compared: {n}\n\n"
        f"Per-code agreement (computed in Python, fixed):\n{table_text}\n\n"
        f"Mean κ across codes with defined κ: "
        + (f"{kappa_mean:.3f}" if kappa_mean is not None else "undefined")
        + f"\nMean percent agreement: {mean_pct:.0%}\n\n"
        f"{_KAPPA_BANDS}\n\nWrite the intercoder agreement report."
    )
    report_md = llm.complete(report_system, report_prompt,
                             model=DEFAULT_PIPELINE_MODEL)

    update_progress(job_id, 0.95, "Saving results")
    new_analysis_id = db.insert("analyses", {
        "kind": "intercoder",
        "study_id": study_id,
        "project_id": study.get("project_id"),
        "title": f"Intercoder agreement — {study.get('title', study_id)}",
        "params": {"analysis_id": analysis_id, "codebook_id": codebook_id},
        "results": {
            "report_md": report_md,
            "kappa_per_code": kappa_per_code,
            "kappa_mean": kappa_mean,
            "n_sessions": n,
            "codebook_id": codebook_id,
            "base_analysis_id": analysis_id,
        },
        "status": "done",
        "completed_at": db.now(),
    })
    return {"analysis_id": new_analysis_id, "kappa_mean": kappa_mean}


@job_task("intercoder")
def _intercoder_job(job_id, study_id=None, analysis_id=None):
    return run_intercoder_analysis(study_id, analysis_id, job_id)


# --------------------------------------------------------------------------
# Code co-occurrence matrix — pure Python, no LLM, runs synchronously.
# --------------------------------------------------------------------------

def cooccurrence(study, params):
    """Session-level code co-occurrence for one codebook.

    `study` is accepted for signature parity with the synchronous quantitative
    kinds (fn(target, params)); the segments are addressed via
    params["codebook_id"]. Counts, for each pair of codes, the number of
    SESSIONS in which both occur; the diagonal is the number of sessions in
    which the code occurs at all.
    """
    codebook_id = (params or {}).get("codebook_id")
    if not codebook_id:
        raise ValueError("Co-occurrence requires params {'codebook_id': ...}.")
    codebook = db.get("codebooks", codebook_id)
    if not codebook:
        raise ValueError(f"Codebook not found: {codebook_id}")
    codes = codebook.get("codes") or []
    if not codes:
        raise ValueError("The selected codebook contains no codes.")
    segments = db.query("coded_segments", "codebook_id = ?", (codebook_id,))
    if not segments:
        raise ValueError(
            "This codebook has no coded segments yet — run a thematic or "
            "deductive coding analysis first.")

    code_ids = [c["id"] for c in codes]
    idx = {cid: i for i, cid in enumerate(code_ids)}
    presence = {}
    for seg in segments:
        if seg["code_id"] in idx:
            presence.setdefault(seg["session_id"], set()).add(seg["code_id"])

    k = len(code_ids)
    matrix = [[0] * k for _ in range(k)]
    for present in presence.values():
        ordered = sorted(present, key=idx.get)
        for pos, ca in enumerate(ordered):
            for cb in ordered[pos:]:
                i, j = idx[ca], idx[cb]
                matrix[i][j] += 1
                if i != j:
                    matrix[j][i] += 1

    pairs = [
        {"code_a": code_ids[i], "code_b": code_ids[j], "count": matrix[i][j]}
        for i in range(k) for j in range(i + 1, k) if matrix[i][j] > 0
    ]
    pairs.sort(key=lambda p: -p["count"])
    return {
        "codes": [{"id": c["id"], "name": c.get("name", c["id"])} for c in codes],
        "matrix": matrix,
        "n_sessions": len(presence),
        "pairs": pairs[:15],
    }


# Synchronous qualitative kinds the integrator can dispatch like the
# quantitative ANALYSIS_KINDS: {kind: (fn(target, params), label)}.
SYNC_KINDS = {
    "cooccurrence": (cooccurrence, "Code co-occurrence"),
}
