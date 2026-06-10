"""Survey builder helpers: AI questionnaire design, question normalization,
answer validation, and response summaries for the dashboard."""

from ...llm import complete_json
from ...config import DEFAULT_PIPELINE_MODEL

QUESTION_TYPES = ["likert", "multiple_choice", "checkbox", "open", "numeric", "matrix", "dropdown"]
CHOICE_TYPES = {"multiple_choice", "checkbox", "dropdown"}
SCALE_TYPES = {"likert", "numeric", "matrix"}

DEFAULT_SCALE = {"min": 1, "max": 5, "min_label": "Strongly disagree", "max_label": "Strongly agree"}


# --- AI survey design --------------------------------------------------------

DESIGN_SYSTEM_PROMPT = """You are an experienced survey methodologist designing a \
self-administered online questionnaire for academic research at a business university. \
Apply established questionnaire-design practice:

- Prefer validated-scale style Likert items (1-5 or 1-7 with labelled endpoints): one \
construct per item, neutral wording, no double-barrelled, leading, or loaded questions.
- Group items that measure the same construct on the same scale into a single matrix \
question (the construct items go in `rows`, rated on `scale`).
- Use multiple_choice or dropdown for single-select categorical facts, checkbox for \
multi-select, numeric for counts/ages/percentages, and open questions sparingly for \
elaboration.
- Order questions from general to specific; place demographic questions at the end.
- Respect the preferred length stated in the researcher's brief; if none is given, aim \
for 10-15 questions.
- `welcome_text` briefly introduces the study, the expected duration, and that responses \
are confidential and used for academic research only. `thankyou_text` is a short, warm \
closing message. Both are plain text, no markdown.
- Question ids must be q1, q2, q3, ... in order. Every question object must include all \
fields: set `options` to [] and `rows` to [] when not applicable, and include a sensible \
`scale` object for likert/numeric/matrix questions (reuse the default 1-5 agreement \
scale for fields where a scale is meaningless)."""

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "welcome_text": {"type": "string"},
        "thankyou_text": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": QUESTION_TYPES},
                    "text": {"type": "string"},
                    "required": {"type": "boolean"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "scale": {
                        "type": "object",
                        "properties": {
                            "min": {"type": "integer"},
                            "max": {"type": "integer"},
                            "min_label": {"type": "string"},
                            "max_label": {"type": "string"},
                        },
                        "required": ["min", "max", "min_label", "max_label"],
                        "additionalProperties": False,
                    },
                    "rows": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "type", "text", "required", "options", "scale", "rows"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["welcome_text", "thankyou_text", "questions"],
    "additionalProperties": False,
}


def design_survey(title, research_question, brief, model=DEFAULT_PIPELINE_MODEL):
    """Generate a full survey config from a researcher's design brief."""
    prompt = (
        f"Design a survey questionnaire.\n\n"
        f"Survey title: {title}\n"
        f"Research question: {research_question or '(not specified)'}\n\n"
        f"Researcher's design brief (target population, constructs to measure, "
        f"preferred length):\n{brief}"
    )
    result = complete_json(DESIGN_SYSTEM_PROMPT, prompt, DESIGN_SCHEMA, model=model)
    return {
        "welcome_text": str(result.get("welcome_text", "")).strip(),
        "thankyou_text": str(result.get("thankyou_text", "")).strip(),
        "questions": normalize_questions(result.get("questions", [])),
    }


# --- question normalization --------------------------------------------------

def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_questions(raw):
    """Coerce a raw questions list into clean Question objects per INTERFACES.md."""
    questions = []
    seen_ids = set()
    for i, q in enumerate(raw or []):
        if not isinstance(q, dict):
            continue
        qtype = q.get("type")
        text = str(q.get("text", "")).strip()
        if qtype not in QUESTION_TYPES or not text:
            continue
        qid = str(q.get("id") or "").strip() or f"q{i + 1}"
        while qid in seen_ids:
            qid += "x"
        seen_ids.add(qid)
        clean = {
            "id": qid,
            "type": qtype,
            "text": text,
            "required": bool(q.get("required", True)),
        }
        if qtype in CHOICE_TYPES:
            clean["options"] = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        if qtype in SCALE_TYPES:
            scale = q.get("scale") or {}
            smin = _int(scale.get("min"), DEFAULT_SCALE["min"])
            smax = _int(scale.get("max"), DEFAULT_SCALE["max"])
            if smax <= smin:
                smin, smax = DEFAULT_SCALE["min"], DEFAULT_SCALE["max"]
            clean["scale"] = {
                "min": smin,
                "max": smax,
                "min_label": str(scale.get("min_label") or "").strip(),
                "max_label": str(scale.get("max_label") or "").strip(),
            }
        if qtype == "matrix":
            clean["rows"] = [str(r).strip() for r in (q.get("rows") or []) if str(r).strip()]
        questions.append(clean)
    return questions


# --- answer validation (respond API) ------------------------------------------

def _number(value):
    n = float(value)
    return int(n) if n.is_integer() else n


def validate_answers(questions, answers):
    """Validate respondent answers; returns (clean_answers, errors)."""
    answers = answers if isinstance(answers, dict) else {}
    clean, errors = {}, []
    for q in questions:
        qid, qtype = q["id"], q["type"]
        value = answers.get(qid)
        if value in (None, "", []):
            if q.get("required"):
                errors.append(f"Question '{q['text']}' is required.")
            continue
        try:
            if qtype in ("likert", "numeric"):
                clean[qid] = _number(value)
            elif qtype in ("multiple_choice", "dropdown"):
                clean[qid] = str(value)
            elif qtype == "checkbox":
                items = value if isinstance(value, list) else [value]
                clean[qid] = [str(v) for v in items]
            elif qtype == "open":
                clean[qid] = str(value).strip()
            elif qtype == "matrix":
                if not isinstance(value, dict):
                    raise ValueError("matrix answer must be an object")
                row_values = {}
                for row in q.get("rows", []):
                    v = value.get(row)
                    if v in (None, ""):
                        if q.get("required"):
                            errors.append(f"Question '{q['text']}': please rate '{row}'.")
                        continue
                    row_values[row] = _number(v)
                if row_values:
                    clean[qid] = row_values
        except (TypeError, ValueError):
            errors.append(f"Question '{q['text']}' received an invalid answer.")
    return clean, errors


# --- dashboard summaries -------------------------------------------------------

def summarize(questions, responses):
    """Per-question summaries: counts per option, means for numeric/likert/matrix."""
    summaries = []
    for q in questions:
        values = []
        for r in responses:
            v = (r.get("answers") or {}).get(q["id"])
            if v not in (None, "", []):
                values.append(v)
        s = {"question": q, "answered": len(values)}
        qtype = q["type"]
        if qtype in ("likert", "numeric"):
            nums = [v for v in values if isinstance(v, (int, float))]
            s["mean"] = round(sum(nums) / len(nums), 2) if nums else None
            if qtype == "likert":
                scale = q.get("scale") or DEFAULT_SCALE
                counts = {}
                for v in nums:
                    counts[int(v)] = counts.get(int(v), 0) + 1
                s["counts"] = [(str(p), counts.get(p, 0))
                               for p in range(scale["min"], scale["max"] + 1)]
        elif qtype in ("multiple_choice", "dropdown", "checkbox"):
            counts = {opt: 0 for opt in q.get("options", [])}
            for v in values:
                picked = v if isinstance(v, list) else [v]
                for p in picked:
                    counts[p] = counts.get(p, 0) + 1
            s["counts"] = list(counts.items())
        elif qtype == "open":
            s["answers"] = [str(v) for v in values]
        elif qtype == "matrix":
            rows = {}
            for row in q.get("rows", []):
                nums = [v[row] for v in values
                        if isinstance(v, dict) and isinstance(v.get(row), (int, float))]
                rows[row] = {"n": len(nums),
                             "mean": round(sum(nums) / len(nums), 2) if nums else None}
            s["rows"] = rows
        summaries.append(s)
    return summaries


def format_answer(question, value):
    """Render one stored answer as a short display string for the response table."""
    if value in (None, "", []):
        return ""
    if question["type"] == "checkbox" and isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if question["type"] == "matrix" and isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
