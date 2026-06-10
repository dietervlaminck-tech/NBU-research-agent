"""Tests for the pure quantitative analysis functions.

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research so the fixture
survey lives in a throwaway SQLite database. No API key is needed: the LLM
interpretation must degrade to None.
"""
import json
import os
import random
import sys
import tempfile

# Make the project root importable regardless of how the tests are invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate the database and make sure no API key leaks in -- BEFORE import.
_TMP = tempfile.mkdtemp(prefix="nbu_test_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db  # noqa: E402
from nbu_research.modules.analysis import quantitative as q  # noqa: E402

QUESTIONS = [
    {"id": "q1", "type": "likert", "text": "I like my job",
     "scale": {"min": 1, "max": 5}},
    {"id": "q2", "type": "likert", "text": "My job is meaningful",
     "scale": {"min": 1, "max": 5}},
    {"id": "q3", "type": "likert", "text": "I feel engaged at work",
     "scale": {"min": 1, "max": 5}},
    {"id": "q4", "type": "numeric", "text": "Hours worked per week",
     "scale": {"min": 0, "max": 80}},
    {"id": "q5", "type": "multiple_choice", "text": "Department",
     "options": ["Sales", "Engineering"]},
    {"id": "q6", "type": "dropdown", "text": "Seniority",
     "options": ["Junior", "Medior", "Senior"]},
    {"id": "q7", "type": "checkbox", "text": "Benefits used",
     "options": ["Gym", "Bonus"]},
    {"id": "q8", "type": "open", "text": "Any comments?"},
    {"id": "q9", "type": "matrix", "text": "Rate your team",
     "rows": ["Trust", "Support"], "scale": {"min": 1, "max": 5}},
]

N = 30
_state = {}


def _setup():
    """Insert one fixture survey study with N synthetic responses (idempotent)."""
    if _state.get("study"):
        return _state["study"]
    db.init_db()
    rng = random.Random(42)
    project_id = db.insert("projects", {"title": "Fixture project"})
    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "survey",
        "title": "Fixture survey",
        "research_question": "How satisfied are employees?",
        "config": {"questions": QUESTIONS, "welcome_text": "", "thankyou_text": ""},
    })
    for i in range(N):
        dept = "Sales" if i % 2 == 0 else "Engineering"
        # Build a group effect for the t-test and correlated likert items.
        base = 4.0 if dept == "Sales" else 2.5
        lik = [max(1, min(5, round(base + rng.gauss(0, 0.7)))) for _ in range(3)]
        answers = {
            "q1": lik[0],
            "q2": max(1, min(5, lik[0] + rng.choice([-1, 0, 0, 1]))),
            "q3": max(1, min(5, lik[0] + rng.choice([-1, 0, 0, 1]))),
            "q4": 30 + 2.0 * lik[0] + rng.gauss(0, 3),
            "q5": dept,
            "q6": rng.choice(["Junior", "Medior", "Senior"]),
            "q7": [opt for opt in ["Gym", "Bonus"] if rng.random() < 0.5],
            "q8": rng.choice(["fine", "ok", "great"]),
            "q9": {"Trust": rng.randint(1, 5), "Support": rng.randint(1, 5)},
        }
        db.insert("survey_responses", {
            "study_id": study_id,
            "respondent_name": f"r{i}",
            "answers": answers,
            "status": "completed",
            "started_at": db.now(),
            "completed_at": db.now(),
        })
    _state["study"] = db.get("studies", study_id)
    return _state["study"]


def _assert_json(results):
    json.dumps(results)  # raises TypeError on numpy leftovers


def test_responses_dataframe():
    study = _setup()
    df = q.responses_dataframe(study)
    assert len(df) == N
    expected = ["q1", "q2", "q3", "q4", "q5", "q6",
                "q7_gym", "q7_bonus", "q8", "q9_trust", "q9_support"]
    assert list(df.columns) == expected
    assert df["q1"].dtype.kind == "f"
    assert df["q9_trust"].dtype.kind == "f"
    assert set(df["q7_gym"].unique()) <= {0.0, 1.0}
    assert df["q5"].map(type).eq(str).all()


def test_dataframe_columns_kinds():
    study = _setup()
    cols = {c["id"]: c["kind"] for c in q.dataframe_columns(study)}
    assert cols["q1"] == "numeric"
    assert cols["q9_support"] == "numeric"
    assert cols["q7_bonus"] == "numeric"
    assert cols["q5"] == "categorical"
    assert cols["q8"] == "categorical"


def test_descriptives():
    study = _setup()
    res = q.descriptives(study, {})
    _assert_json(res)
    assert res["n_responses"] == N
    s = res["numeric"]["q1"]
    assert s["n"] == N
    assert 1 <= s["mean"] <= 5
    assert s["min"] <= s["median"] <= s["max"]
    freqs = res["categorical"]["q5"]
    assert sum(f["count"] for f in freqs) == N
    assert {f["value"] for f in freqs} == {"Sales", "Engineering"}


def test_reliability():
    study = _setup()
    res = q.reliability(study, {"items": ["q1", "q2", "q3"]})
    _assert_json(res)
    assert res["n_items"] == 3 and res["n"] == N
    assert 0.5 < res["alpha"] <= 1.0  # items built to correlate
    assert len(res["items"]) == 3
    for item in res["items"]:
        assert item["item_total_r"] > 0
        assert item["alpha_if_deleted"] is not None


def test_ttest():
    study = _setup()
    res = q.ttest(study, {"dv": "q1", "group_col": "q5"})
    _assert_json(res)
    assert len(res["groups"]) == 2
    assert res["df"] == N - 2
    assert res["p"] < 0.01  # strong built-in group effect
    assert abs(res["cohens_d"]) > 0.8
    means = {g["group"]: g["mean"] for g in res["groups"]}
    assert means["Sales"] > means["Engineering"]


def test_ttest_errors_on_three_groups():
    study = _setup()
    try:
        q.ttest(study, {"dv": "q1", "group_col": "q6"})
        assert False, "expected ValueError for 3 groups"
    except ValueError as e:
        assert "exactly 2 groups" in str(e)
        assert "ANOVA" in str(e)


def test_anova():
    study = _setup()
    res = q.anova(study, {"dv": "q1", "factor": "q6"})
    _assert_json(res)
    assert len(res["groups"]) == 3
    assert res["df_between"] == 2
    assert res["df_within"] == N - 3
    assert res["F"] >= 0
    assert 0 <= res["eta_squared"] <= 1


def test_correlation():
    study = _setup()
    items = ["q1", "q2", "q4"]
    res = q.correlation(study, {"items": items})
    _assert_json(res)
    assert res["items"] == items and res["n"] == N
    k = len(items)
    for i in range(k):
        assert res["r"][i][i] == 1.0
        for j in range(k):
            assert res["r"][i][j] == res["r"][j][i]
            assert -1 <= res["r"][i][j] <= 1
            assert 0 <= res["p"][i][j] <= 1
    assert res["r"][0][1] > 0.4  # q1/q2 built to correlate


def test_regression():
    study = _setup()
    res = q.regression(study, {"dv": "q4", "ivs": ["q1", "q9_trust"]})
    _assert_json(res)
    assert res["n"] == N
    terms = [c["term"] for c in res["coefficients"]]
    assert terms == ["Intercept", "q1", "q9_trust"]
    assert 0 <= res["r_squared"] <= 1
    assert res["adj_r_squared"] <= res["r_squared"]
    q1_coef = next(c for c in res["coefficients"] if c["term"] == "q1")
    assert q1_coef["coef"] > 0  # q4 built as 30 + 2*q1 + noise
    assert q1_coef["p"] < 0.05


def test_crosstab():
    study = _setup()
    res = q.crosstab(study, {"var1": "q5", "var2": "q6"})
    _assert_json(res)
    assert res["n"] == N
    assert sum(sum(r) for r in res["counts"]) == N
    assert res["chi2"] >= 0
    assert 0 <= res["p"] <= 1
    assert 0 <= res["cramers_v"] <= 1
    assert res["df"] == (len(res["row_labels"]) - 1) * (len(res["col_labels"]) - 1)


def test_run_analysis_persists_and_interpretation_is_none_safe():
    study = _setup()
    analysis_id = q.run_analysis(study, "ttest", {"dv": "q1", "group_col": "q5"})
    row = db.get("analyses", analysis_id)
    assert row is not None
    assert row["kind"] == "ttest" and row["status"] == "done"
    assert row["study_id"] == study["id"]
    assert row["completed_at"]
    assert row["results"]["interpretation"] is None  # no API key configured
    _assert_json(row["results"])


def test_result_tables_render():
    study = _setup()
    for kind, params in [
        ("descriptives", {}),
        ("reliability", {"items": ["q1", "q2", "q3"]}),
        ("ttest", {"dv": "q1", "group_col": "q5"}),
        ("anova", {"dv": "q1", "factor": "q6"}),
        ("correlation", {"items": ["q1", "q2"]}),
        ("regression", {"dv": "q4", "ivs": ["q1"]}),
        ("crosstab", {"var1": "q5", "var2": "q6"}),
    ]:
        fn, _ = q.ANALYSIS_KINDS[kind]
        tables = q.result_tables(kind, fn(study, params))
        assert tables, f"no tables for {kind}"
        for t in tables:
            assert t["title"] and t["columns"]
            for row in t["rows"]:
                assert len(row) == len(t["columns"])
                assert all(isinstance(c, str) for c in row)


def _main():
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    _main()
