"""Survey builder helpers: AI questionnaire design, question normalization,
answer validation, and response summaries for the dashboard."""

from ...llm import complete_json
from ...config import DEFAULT_PIPELINE_MODEL

QUESTION_TYPES = ["likert", "multiple_choice", "checkbox", "open", "numeric", "matrix", "dropdown"]
CHOICE_TYPES = {"multiple_choice", "checkbox", "dropdown"}
SCALE_TYPES = {"likert", "numeric", "matrix"}
SHOW_IF_OPS = {"equals", "not_equals", "in", "gte", "lte"}

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


def _normalize_show_if(raw_cond, earlier_ids):
    """Clean a v0.2 show_if condition; returns None when invalid/empty.

    Invalid conditions (unknown op, missing/unknown/later question reference)
    are stripped rather than rejected, consistent with the coerce-don't-error
    style of normalize_questions.
    """
    if not isinstance(raw_cond, dict):
        return None
    ref = str(raw_cond.get("question") or "").strip()
    op = str(raw_cond.get("op") or "").strip()
    if not ref or ref not in earlier_ids or op not in SHOW_IF_OPS:
        return None
    value = raw_cond.get("value")
    if op == "in":
        # value is a list of accepted answers; a comma-separated string is split.
        if isinstance(value, list):
            value = [str(v).strip() for v in value if str(v).strip()]
        else:
            value = [v.strip() for v in str(value or "").split(",") if v.strip()]
        if not value:
            return None
    else:
        value = value if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else str(value if value is not None else "").strip()
        if value == "":
            return None
    return {"question": ref, "op": op, "value": value}


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
        clean = {
            "id": qid,
            "type": qtype,
            "text": text,
            "required": bool(q.get("required", True)),
        }
        # v0.2 optional fields — omitted entirely at their defaults so legacy
        # survey configs round-trip unchanged.
        page = _int(q.get("page"), 1)
        if page > 1:
            clean["page"] = page
        show_if = _normalize_show_if(q.get("show_if"), seen_ids)
        if show_if:
            clean["show_if"] = show_if
        if qtype in CHOICE_TYPES and bool(q.get("randomize_options")):
            clean["randomize_options"] = True
        construct = str(q.get("construct") or "").strip()
        if construct:
            clean["construct"] = construct
        seen_ids.add(qid)
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


def _values_equal(a, b):
    """Loose equality for show_if: numeric when both sides parse, else string.
    A list answer (checkbox) matches when any selected option matches."""
    if isinstance(a, list):
        return any(_values_equal(x, b) for x in a)
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def condition_met(cond, answers):
    """Evaluate one show_if condition against an answers dict.

    An unanswered (or itself hidden) referenced question never matches, so
    dependents of hidden questions stay hidden. Mirrors the client-side
    evaluator in run.html — keep the two in sync.
    """
    ref = answers.get(cond.get("question"))
    if ref in (None, "", []):
        return False
    op, value = cond.get("op"), cond.get("value")
    if op == "equals":
        return _values_equal(ref, value)
    if op == "not_equals":
        return not _values_equal(ref, value)
    if op == "in":
        accepted = value if isinstance(value, list) else [value]
        return any(_values_equal(ref, v) for v in accepted)
    if op in ("gte", "lte"):
        try:
            r, v = float(ref), float(value)
        except (TypeError, ValueError):
            return False
        return r >= v if op == "gte" else r <= v
    return False


def validate_answers(questions, answers):
    """Validate respondent answers; returns (clean_answers, errors).

    v0.2: show_if is re-evaluated server-side against the already-validated
    earlier answers (never trust the client) — logic-hidden questions are not
    required and any smuggled answers for them are dropped. show_if may only
    reference earlier questions, so a single forward pass suffices.
    """
    answers = answers if isinstance(answers, dict) else {}
    clean, errors = {}, []
    for q in questions:
        qid, qtype = q["id"], q["type"]
        if q.get("show_if") and not condition_met(q["show_if"], clean):
            continue  # hidden: never required; smuggled answers dropped
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
