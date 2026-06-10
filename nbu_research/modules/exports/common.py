"""Shared helpers for the export module."""
import re

# Map chat roles to transcript speaker labels (AI asks, respondent answers).
SPEAKERS = {"assistant": "Interviewer", "user": "Respondent"}


def slugify(text, fallback="export"):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return s or fallback


def speaker(role):
    return SPEAKERS.get(role, str(role or "Speaker").title())


def transcript_text(session):
    """Full plain-text transcript of one interview session."""
    lines = []
    for msg in session.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        lines.append(f"{speaker(msg.get('role'))}: {msg.get('content', '')}")
    return "\n\n".join(lines)


def question_id(q, index):
    return q.get("id") or f"q{index + 1}"


def survey_columns(questions):
    """Flatten survey questions to columns: [(key, label, question, matrix_row)].

    Matrix questions become one `qid_row` column per row; all other types map
    to a single column keyed by the question id.
    """
    cols = []
    for i, q in enumerate(questions):
        qid = question_id(q, i)
        text = q.get("text", "")
        if q.get("type") == "matrix":
            for row in q.get("rows") or []:
                cols.append((f"{qid}_{row}", f"{text} — {row}", q, row))
        else:
            cols.append((qid, text, q, None))
    return cols


def answer_value(q, matrix_row, answers, qid):
    """Extract one cell value from a response's answers dict."""
    val = (answers or {}).get(qid)
    if q.get("type") == "matrix":
        return val.get(matrix_row) if isinstance(val, dict) else None
    if q.get("type") == "checkbox":
        if isinstance(val, list):
            return ";".join(str(v) for v in val)
        return val
    return val
