"""Tabular study exports: JSON dump, CSV and XLSX data matrices."""
import io
import json

import pandas as pd

from ... import db
from .common import slugify, transcript_text, survey_columns, answer_value, question_id

RESPONSE_META = ["response_id", "respondent_name", "status", "started_at", "completed_at"]
SESSION_META = ["session_id", "respondent_name", "status", "started_at",
                "completed_at", "duration_seconds"]


def _survey_frame(study):
    """Wide respondent x question matrix: one row per response."""
    questions = (study.get("config") or {}).get("questions") or []
    responses = db.query("survey_responses", "study_id = ?",
                         (study.get("id", ""),), order="started_at")
    cols = survey_columns(questions)
    qids = {id(q): question_id(q, i) for i, q in enumerate(questions)}
    rows = []
    for r in responses:
        row = {
            "response_id": r.get("id"),
            "respondent_name": r.get("respondent_name", ""),
            "status": r.get("status", ""),
            "started_at": r.get("started_at", ""),
            "completed_at": r.get("completed_at", ""),
        }
        for key, _label, q, mrow in cols:
            row[key] = answer_value(q, mrow, r.get("answers"), qids[id(q)])
        rows.append(row)
    return pd.DataFrame(rows, columns=RESPONSE_META + [c[0] for c in cols])


def _interview_frame(study):
    """One row per interview session, with metadata and full transcript."""
    sessions = db.query("sessions", "study_id = ?",
                        (study.get("id", ""),), order="started_at")
    rows = []
    for s in sessions:
        rows.append({
            "session_id": s.get("id"),
            "respondent_name": s.get("respondent_name", ""),
            "status": s.get("status", ""),
            "started_at": s.get("started_at", ""),
            "completed_at": s.get("completed_at", ""),
            "duration_seconds": s.get("duration_seconds", 0),
            "transcript": transcript_text(s),
        })
    return pd.DataFrame(rows, columns=SESSION_META + ["transcript"])


def study_frame(study):
    if study.get("study_type") == "survey":
        return _survey_frame(study)
    return _interview_frame(study)


def study_json(study_id):
    study = db.get("studies", study_id) or {}
    sid = study.get("id", study_id)
    codebooks = db.query("codebooks", "study_id = ?", (sid,))
    segments = []
    for cb in codebooks:
        segments += db.query("coded_segments", "codebook_id = ?", (cb["id"],))
    dump = {
        "study": study,
        "sessions": db.query("sessions", "study_id = ?", (sid,), order="started_at"),
        "survey_responses": db.query("survey_responses", "study_id = ?",
                                     (sid,), order="started_at"),
        "codebooks": codebooks,
        "coded_segments": segments,
    }
    data = json.dumps(dump, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    return data, f"{slugify(study.get('title'))}.json", "application/json"


def study_csv(study_id):
    study = db.get("studies", study_id) or {}
    df = study_frame(study)
    data = df.to_csv(index=False).encode("utf-8")
    return data, f"{slugify(study.get('title'))}.csv", "text/csv"


def study_xlsx(study_id):
    study = db.get("studies", study_id) or {}
    df = study_frame(study)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
    mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return buf.getvalue(), f"{slugify(study.get('title'))}.xlsx", mimetype
