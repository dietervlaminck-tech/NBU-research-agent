"""Surveys v0.2: validated scale library, multi-page, skip logic, randomization.

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research so fixture
surveys land in a throwaway SQLite database; auth env vars are stripped so the
app runs in dev mode (synthetic user, no login guard).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="nbu_test_surveys_v02_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)
for _var in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID", "AZURE_REDIRECT_URI"):
    os.environ.pop(_var, None)

from nbu_research import db, create_app  # noqa: E402
from nbu_research.modules.surveys.builder import (  # noqa: E402
    SHOW_IF_OPS,
    condition_met,
    normalize_questions,
    validate_answers,
)
from nbu_research.modules.surveys.scales import SCALE_LIBRARY  # noqa: E402

db.init_db()

APP = create_app()
APP.config["TESTING"] = True


# --- scale library -----------------------------------------------------------

def test_scale_library_shape():
    assert {"uwes9", "tam_pu", "tam_peou", "bfi10", "pss4"} <= set(SCALE_LIBRARY)
    for key, s in SCALE_LIBRARY.items():
        assert s["name"] and s["citation"], key
        assert s["items"] and all(isinstance(i, str) and i for i in s["items"]), key
        scale = s["scale"]
        assert set(scale) == {"min", "max", "min_label", "max_label"}, key
        assert isinstance(scale["min"], int) and isinstance(scale["max"], int)
        assert scale["min"] < scale["max"], key


def test_scale_library_published_specs():
    assert len(SCALE_LIBRARY["uwes9"]["items"]) == 9
    assert SCALE_LIBRARY["uwes9"]["scale"]["min"] == 0
    assert SCALE_LIBRARY["uwes9"]["scale"]["max"] == 6
    assert len(SCALE_LIBRARY["tam_pu"]["items"]) == 6
    assert len(SCALE_LIBRARY["tam_peou"]["items"]) == 6
    assert SCALE_LIBRARY["tam_pu"]["scale"]["max"] - SCALE_LIBRARY["tam_pu"]["scale"]["min"] == 6  # 7-point
    assert len(SCALE_LIBRARY["bfi10"]["items"]) == 10
    assert any("(R)" in i for i in SCALE_LIBRARY["bfi10"]["items"])  # reversed items marked
    assert SCALE_LIBRARY["bfi10"]["scale"] == {"min": 1, "max": 5,
                                               "min_label": "Disagree strongly",
                                               "max_label": "Agree strongly"}
    assert len(SCALE_LIBRARY["pss4"]["items"]) == 4
    assert sum("(R)" in i for i in SCALE_LIBRARY["pss4"]["items"]) == 2


# --- normalize_questions: v0.2 round-trip & rejection --------------------------

def test_normalize_roundtrips_v02_fields():
    raw = [
        {"id": "q1", "type": "multiple_choice", "text": "Do you use AI tools?",
         "options": ["Yes", "No"], "randomize_options": True},
        {"id": "q2", "type": "likert", "text": "AI improves my productivity.",
         "page": 2, "construct": "tam_pu",
         "scale": {"min": 1, "max": 7, "min_label": "Disagree", "max_label": "Agree"},
         "show_if": {"question": "q1", "op": "equals", "value": "Yes"}},
    ]
    out = normalize_questions(raw)
    assert out[0]["randomize_options"] is True
    assert "page" not in out[0]                      # default page 1 stays implicit
    assert out[1]["page"] == 2
    assert out[1]["construct"] == "tam_pu"
    assert out[1]["show_if"] == {"question": "q1", "op": "equals", "value": "Yes"}
    # Round-trip: normalizing the normalized output is a no-op.
    assert normalize_questions(out) == out


def test_normalize_show_if_in_op_and_page_validation():
    raw = [
        {"id": "q1", "type": "dropdown", "text": "Role", "options": ["Manager", "Analyst", "Other"]},
        {"id": "q2", "type": "open", "text": "Describe your team.", "page": 0,
         "show_if": {"question": "q1", "op": "in", "value": "Manager, Analyst"}},
    ]
    out = normalize_questions(raw)
    assert "page" not in out[1]                       # page<1 coerced back to default
    assert out[1]["show_if"]["value"] == ["Manager", "Analyst"]  # "in" value is a list


def test_normalize_rejects_bad_show_if():
    later_ref = normalize_questions([
        {"id": "q1", "type": "open", "text": "A",
         "show_if": {"question": "q2", "op": "equals", "value": "x"}},   # later question
        {"id": "q2", "type": "open", "text": "B"},
    ])
    assert "show_if" not in later_ref[0]

    self_ref = normalize_questions([
        {"id": "q1", "type": "open", "text": "A",
         "show_if": {"question": "q1", "op": "equals", "value": "x"}},
    ])
    assert "show_if" not in self_ref[0]

    bad_op = normalize_questions([
        {"id": "q1", "type": "open", "text": "A"},
        {"id": "q2", "type": "open", "text": "B",
         "show_if": {"question": "q1", "op": "contains", "value": "x"}},  # unknown op
    ])
    assert "show_if" not in bad_op[1]

    empty = normalize_questions([
        {"id": "q1", "type": "open", "text": "A"},
        {"id": "q2", "type": "open", "text": "B", "show_if": {}},          # empty → stripped
    ])
    assert "show_if" not in empty[1]


def test_normalize_randomize_options_choice_types_only():
    out = normalize_questions([
        {"id": "q1", "type": "likert", "text": "A", "randomize_options": True},
        {"id": "q2", "type": "checkbox", "text": "B", "options": ["x", "y"],
         "randomize_options": True},
    ])
    assert "randomize_options" not in out[0]
    assert out[1]["randomize_options"] is True


def test_normalize_legacy_questions_unchanged():
    legacy = [{"id": "q1", "type": "likert", "text": "Old question", "required": True,
               "scale": {"min": 1, "max": 5, "min_label": "No", "max_label": "Yes"}}]
    out = normalize_questions(legacy)
    assert out == legacy   # no v0.2 keys injected into legacy configs


# --- validate_answers: server-side skip logic -----------------------------------

SKIP_FIXTURE = normalize_questions([
    {"id": "q1", "type": "multiple_choice", "text": "Do you use AI tools?",
     "options": ["Yes", "No"], "required": True},
    {"id": "q2", "type": "likert", "text": "How satisfied are you with them?",
     "required": True, "scale": {"min": 1, "max": 5, "min_label": "Not", "max_label": "Very"},
     "show_if": {"question": "q1", "op": "equals", "value": "Yes"}},
])


def test_hidden_question_answers_dropped_and_not_required():
    # q1 = "No" hides q2; a smuggled q2 answer must be DROPPED, no error raised.
    clean, errors = validate_answers(SKIP_FIXTURE, {"q1": "No", "q2": 5})
    assert errors == []
    assert clean == {"q1": "No"}
    assert "q2" not in clean


def test_visible_question_still_required():
    clean, errors = validate_answers(SKIP_FIXTURE, {"q1": "Yes"})
    assert any("required" in e for e in errors)

    clean, errors = validate_answers(SKIP_FIXTURE, {"q1": "Yes", "q2": 4})
    assert errors == []
    assert clean == {"q1": "Yes", "q2": 4}


def test_chained_hiding_and_ops():
    questions = normalize_questions([
        {"id": "q1", "type": "multiple_choice", "text": "Employed?",
         "options": ["Yes", "No"], "required": True},
        {"id": "q2", "type": "likert", "text": "Engagement",
         "scale": {"min": 0, "max": 6, "min_label": "", "max_label": ""},
         "show_if": {"question": "q1", "op": "equals", "value": "Yes"}, "required": True},
        {"id": "q3", "type": "open", "text": "Why so engaged?", "required": True,
         "show_if": {"question": "q2", "op": "gte", "value": "5"}},
    ])
    # q1=No hides q2, whose missing answer in turn hides q3 (chained).
    clean, errors = validate_answers(questions, {"q1": "No", "q2": 6, "q3": "smuggled"})
    assert errors == [] and clean == {"q1": "No"}
    # q2=4 < 5 keeps q3 hidden.
    clean, errors = validate_answers(questions, {"q1": "Yes", "q2": 4})
    assert errors == [] and clean == {"q1": "Yes", "q2": 4}
    # q2=5 reveals q3 → required.
    clean, errors = validate_answers(questions, {"q1": "Yes", "q2": 5})
    assert any("required" in e for e in errors)


def test_condition_met_ops():
    assert condition_met({"question": "q", "op": "equals", "value": "5"}, {"q": 5})
    assert condition_met({"question": "q", "op": "not_equals", "value": "No"}, {"q": "Yes"})
    assert not condition_met({"question": "q", "op": "not_equals", "value": "No"}, {})  # unanswered → hidden
    assert condition_met({"question": "q", "op": "in", "value": ["A", "B"]}, {"q": "B"})
    assert condition_met({"question": "q", "op": "in", "value": ["A", "B"]}, {"q": ["C", "A"]})  # checkbox
    assert condition_met({"question": "q", "op": "lte", "value": "3"}, {"q": 2})
    assert not condition_met({"question": "q", "op": "gte", "value": "3"}, {"q": "abc"})
    assert "contains" not in SHOW_IF_OPS


# --- API + render checks ----------------------------------------------------------

def _make_survey(config):
    return db.insert("studies", {"project_id": None, "study_type": "survey",
                                 "title": "V02 fixture", "config": config})


def test_save_endpoint_roundtrips_and_derives_citations():
    sid = _make_survey({"questions": [], "welcome_text": "", "thankyou_text": ""})
    client = APP.test_client()
    scale = SCALE_LIBRARY["uwes9"]
    questions = [{"id": "q1", "type": "multiple_choice", "text": "Employed?",
                  "options": ["Yes", "No"], "required": True}]
    questions += [{"id": f"q{i + 2}", "type": "likert", "text": item, "required": True,
                   "scale": dict(scale["scale"]), "construct": "uwes9", "page": 2,
                   "show_if": ({"question": "q1", "op": "equals", "value": "Yes"} if i == 0 else None)}
                  for i, item in enumerate(scale["items"])]
    res = client.post(f"/surveys/api/{sid}/questions", json={
        "questions": questions, "welcome_text": "w", "thankyou_text": "t",
        "randomize_questions": True,
    })
    assert res.status_code == 200
    config = db.get("studies", sid)["config"]
    assert config["randomize_questions"] is True
    assert config["scale_citations"] == {"uwes9": scale["citation"]}
    assert len(config["questions"]) == 10
    assert config["questions"][1]["page"] == 2
    assert config["questions"][1]["construct"] == "uwes9"
    assert config["questions"][1]["show_if"]["question"] == "q1"
    assert "show_if" not in config["questions"][2]


def test_builder_and_runner_render_v02_survey():
    scale = SCALE_LIBRARY["pss4"]
    questions = normalize_questions(
        [{"id": "q1", "type": "multiple_choice", "text": "Do you consent?",
          "options": ["Yes", "No"], "required": True}] +
        [{"id": f"q{i + 2}", "type": "likert", "text": item, "required": True,
          "scale": dict(scale["scale"]), "construct": "pss4", "page": 2,
          "show_if": {"question": "q1", "op": "equals", "value": "Yes"} if i == 0 else None}
         for i, item in enumerate(scale["items"])])
    sid = _make_survey({"questions": questions, "welcome_text": "Welcome",
                        "thankyou_text": "Bye", "randomize_questions": True,
                        "scale_citations": {"pss4": scale["citation"]}})
    client = APP.test_client()

    res = client.get(f"/surveys/builder/{sid}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Insert Validated Scale" in html
    assert "Randomize question order" in html
    assert "unable to" in html  # PSS-4 item text reached the builder

    res = client.get(f"/surveys/run/{sid}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-page=\"2\"" in html
    assert "'Page ' + (i + 1) + ' of '" in html      # progress indicator wiring
    assert "unable to control the important things" in html
    assert "RANDOMIZE_QUESTIONS = true" in html

    res = client.get(f"/surveys/dashboard/{sid}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Validated Instruments" in html
    assert "Cohen" in html                            # citation shown


def test_legacy_survey_still_renders_and_validates():
    legacy_questions = [
        {"id": "q1", "type": "likert", "text": "Legacy likert question", "required": True,
         "scale": {"min": 1, "max": 5, "min_label": "Low", "max_label": "High"}},
        {"id": "q2", "type": "open", "text": "Legacy open question", "required": False},
    ]
    sid = _make_survey({"questions": legacy_questions, "welcome_text": "", "thankyou_text": ""})
    client = APP.test_client()

    for path in (f"/surveys/builder/{sid}", f"/surveys/run/{sid}", f"/surveys/dashboard/{sid}"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "Legacy likert question" in res.get_data(as_text=True), path
    html = client.get(f"/surveys/run/{sid}").get_data(as_text=True)
    assert "RANDOMIZE_QUESTIONS = false" in html
    assert "Validated Instruments" not in client.get(f"/surveys/dashboard/{sid}").get_data(as_text=True)

    # Answer payload format unchanged; submission stores a response.
    res = client.post(f"/surveys/api/{sid}/respond",
                      json={"answers": {"q1": 4, "q2": "fine"}})
    assert res.status_code == 200
    stored = db.query("survey_responses", "study_id = ?", (sid,), order="started_at DESC")
    assert stored and stored[0]["answers"] == {"q1": 4, "q2": "fine"}


def test_respond_endpoint_drops_smuggled_hidden_answers():
    sid = _make_survey({"questions": SKIP_FIXTURE, "welcome_text": "", "thankyou_text": ""})
    client = APP.test_client()
    res = client.post(f"/surveys/api/{sid}/respond",
                      json={"answers": {"q1": "No", "q2": 5}})
    assert res.status_code == 200
    stored = db.query("survey_responses", "study_id = ?", (sid,), order="started_at DESC")
    assert stored[0]["answers"] == {"q1": "No"}

    res = client.post(f"/surveys/api/{sid}/respond", json={"answers": {"q1": "Yes"}})
    assert res.status_code == 400
    assert any("required" in e for e in res.get_json()["errors"])
