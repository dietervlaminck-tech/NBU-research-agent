"""Tests for the v0.2 quantitative analysis kinds (efa, manova, mannwhitney,
kruskal, wilcoxon, effect_sizes).

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

LIKERT = {"min": 1, "max": 7}
QUESTIONS = [
    {"id": "q1", "type": "likert", "text": "Item A1", "scale": LIKERT},
    {"id": "q2", "type": "likert", "text": "Item A2", "scale": LIKERT},
    {"id": "q3", "type": "likert", "text": "Item A3", "scale": LIKERT},
    {"id": "q4", "type": "likert", "text": "Item B1", "scale": LIKERT},
    {"id": "q5", "type": "likert", "text": "Item B2", "scale": LIKERT},
    {"id": "q6", "type": "likert", "text": "Item B3", "scale": LIKERT},
    {"id": "q7", "type": "multiple_choice", "text": "Campus",
     "options": ["North", "South", "Online"]},
    {"id": "q8", "type": "dropdown", "text": "Role",
     "options": ["Faculty", "Staff"]},
]

N = 60
_state = {}


def _clip(v):
    return max(1, min(7, round(v)))


def _setup():
    """One fixture survey with N=60 structured synthetic responses (idempotent).

    Structure: q1-q3 load on latent factor A, q4-q6 on independent factor B
    (so EFA finds 2 factors by Kaiser); the binary q8 shifts factor A (group
    effect for ttest/mannwhitney); the 3-level q7 shifts factor B (effect for
    anova/kruskal/manova); q4 sits clearly above q1 (paired wilcoxon signal).
    """
    if _state.get("study"):
        return _state["study"]
    db.init_db()
    rng = random.Random(7)
    project_id = db.insert("projects", {"title": "v0.2 fixture project"})
    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "survey",
        "title": "v0.2 fixture survey",
        "research_question": "Does role shape engagement?",
        "config": {"questions": QUESTIONS, "welcome_text": "", "thankyou_text": ""},
    })
    campuses = ["North", "South", "Online"]
    for i in range(N):
        role = "Faculty" if i % 2 == 0 else "Staff"
        campus = campuses[i % 3]
        factor_a = (4.8 if role == "Faculty" else 3.0) + rng.gauss(0, 1.0)
        factor_b = {"North": 5.6, "South": 4.6, "Online": 3.6}[campus] + rng.gauss(0, 1.0)
        answers = {
            "q1": _clip(factor_a + rng.gauss(0, 0.6)),
            "q2": _clip(factor_a + rng.gauss(0, 0.6)),
            "q3": _clip(factor_a + rng.gauss(0, 0.6)),
            "q4": _clip(factor_b + rng.gauss(0, 0.6)),
            "q5": _clip(factor_b + rng.gauss(0, 0.6)),
            "q6": _clip(factor_b + rng.gauss(0, 0.6)),
            "q7": campus,
            "q8": role,
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


def _empty_study():
    """A second study with zero responses and zero analyses (idempotent)."""
    if _state.get("empty"):
        return _state["empty"]
    db.init_db()
    project_id = db.insert("projects", {"title": "Empty fixture"})
    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "survey",
        "title": "Empty survey",
        "config": {"questions": QUESTIONS, "welcome_text": "", "thankyou_text": ""},
    })
    _state["empty"] = db.get("studies", study_id)
    return _state["empty"]


def _assert_json(results):
    json.dumps(results)  # raises TypeError on numpy leftovers


ITEMS6 = ["q1", "q2", "q3", "q4", "q5", "q6"]


# --- efa ----------------------------------------------------------------------

def test_efa_auto_finds_two_factors():
    study = _setup()
    res = q.efa(study, {"items": ITEMS6, "n_factors": "auto"})
    _assert_json(res)
    assert res["n"] == N
    assert res["n_factors"] == 2  # Kaiser: two latent factors built in
    assert "Kaiser" in res["n_factors_method"]
    # Eigenvalues: one per item, exactly two above 1.
    assert len(res["eigenvalues"]) == 6
    assert sum(1 for ev in res["eigenvalues"] if ev > 1) == 2
    # Loadings table: 6 items x 2 factors.
    assert len(res["loadings"]) == 6
    for row in res["loadings"]:
        assert row["item"] in ITEMS6
        assert len(row["values"]) == 2
    # Diagnostics present and sane.
    assert 0 <= res["kmo"] <= 1
    assert set(res["kmo_per_item"]) == set(ITEMS6)
    assert res["bartlett_chi2"] > 0
    assert res["bartlett_df"] == 15  # 6*5/2
    assert res["bartlett_p"] < 0.001  # strongly correlated items
    assert len(res["communalities"]) == 6
    assert all(0 <= c["communality"] <= 1.05 for c in res["communalities"])
    assert len(res["variance"]) == 2
    cum = [v["cumulative_pct"] for v in res["variance"]]
    assert cum == sorted(cum) and 0 < cum[-1] <= 100


def test_efa_items_separate_onto_their_factors():
    study = _setup()
    res = q.efa(study, {"items": ITEMS6})  # n_factors absent -> auto
    by_item = {l["item"]: l["values"] for l in res["loadings"]}
    # Each block's items must share a dominant factor, opposite blocks differ.
    dom_a = {ITEMS6[i]: max(range(2), key=lambda j: abs(by_item[ITEMS6[i]][j]))
             for i in range(3)}
    dom_b = {ITEMS6[i]: max(range(2), key=lambda j: abs(by_item[ITEMS6[i]][j]))
             for i in range(3, 6)}
    assert len(set(dom_a.values())) == 1
    assert len(set(dom_b.values())) == 1
    assert set(dom_a.values()) != set(dom_b.values())


def test_efa_fixed_n_factors():
    study = _setup()
    res = q.efa(study, {"items": ITEMS6, "n_factors": 3})
    assert res["n_factors"] == 3
    assert res["n_factors_method"] == "specified"
    assert all(len(l["values"]) == 3 for l in res["loadings"])


def test_efa_errors():
    study = _setup()
    try:
        q.efa(study, {"items": ["q1"]})
        assert False, "expected ValueError for 1 item"
    except ValueError as e:
        assert "at least 3 items" in str(e) and "1" in str(e)
    try:
        q.efa(study, {"items": ITEMS6, "n_factors": 6})
        assert False, "expected ValueError for n_factors == n_items"
    except ValueError as e:
        assert "between 1 and 5" in str(e)
    try:
        q.efa(study, {"items": ["q1", "q2", "nope"]})
        assert False, "expected ValueError for unknown item"
    except ValueError as e:
        assert "nope" in str(e)


# --- manova ---------------------------------------------------------------------

def test_manova():
    study = _setup()
    res = q.manova(study, {"dvs": ["q1", "q4"], "factor": "q7"})
    _assert_json(res)
    assert res["n"] == N
    names = {t["test"] for t in res["tests"]}
    assert any("Wilks" in n for n in names)
    assert any("Pillai" in n for n in names)
    assert any("Hotelling" in n for n in names)
    assert any("Roy" in n for n in names)
    assert len(res["tests"]) == 4
    for t in res["tests"]:
        assert t["F"] >= 0
        assert 0 <= t["p"] <= 1
        assert t["df_num"] > 0 and t["df_den"] > 0
    # q7 strongly shifts factor B (q4) -> the MANOVA must detect it.
    wilks = next(t for t in res["tests"] if "Wilks" in t["test"])
    assert wilks["p"] < 0.01
    # Group means per DV, one row per campus.
    assert {g["group"] for g in res["groups"]} == {"North", "South", "Online"}
    for g in res["groups"]:
        assert set(g["means"]) == {"q1", "q4"}
        assert g["n"] >= 2
    means_q4 = {g["group"]: g["means"]["q4"] for g in res["groups"]}
    assert means_q4["North"] > means_q4["Online"]


def test_manova_errors():
    study = _setup()
    try:
        q.manova(study, {"dvs": ["q1"], "factor": "q7"})
        assert False, "expected ValueError for 1 DV"
    except ValueError as e:
        assert "at least 2 dependent variables" in str(e)
    try:
        q.manova(study, {"dvs": ["q1", "q4"], "factor": ""})
        assert False, "expected ValueError for missing factor"
    except ValueError:
        pass


# --- mannwhitney ------------------------------------------------------------------

def test_mannwhitney():
    study = _setup()
    res = q.mannwhitney(study, {"dv": "q1", "group_col": "q8"})
    _assert_json(res)
    assert len(res["groups"]) == 2
    assert {g["group"] for g in res["groups"]} == {"Faculty", "Staff"}
    assert sum(g["n"] for g in res["groups"]) == N
    assert res["U"] >= 0
    assert res["p"] < 0.01  # strong built-in group effect
    assert -1 <= res["rank_biserial"] <= 1
    assert abs(res["rank_biserial"]) > 0.3
    medians = {g["group"]: g["median"] for g in res["groups"]}
    assert medians["Faculty"] > medians["Staff"]
    # r = 1 - 2U/(n1*n2) exactly (allowing for rounding via _py).
    n1, n2 = (g["n"] for g in res["groups"])
    assert abs(res["rank_biserial"] - (1 - 2 * res["U"] / (n1 * n2))) < 1e-3


def test_mannwhitney_errors_on_three_groups():
    study = _setup()
    try:
        q.mannwhitney(study, {"dv": "q1", "group_col": "q7"})
        assert False, "expected ValueError for 3 groups"
    except ValueError as e:
        assert "exactly 2 groups" in str(e)
        assert "Kruskal" in str(e)


# --- kruskal ----------------------------------------------------------------------

def test_kruskal():
    study = _setup()
    res = q.kruskal(study, {"dv": "q4", "factor": "q7"})
    _assert_json(res)
    assert res["n"] == N
    assert len(res["groups"]) == 3
    assert res["df"] == 2
    assert res["H"] >= 0
    assert res["p"] < 0.01  # campus strongly shifts q4
    assert 0 <= res["epsilon_squared"] <= 1
    medians = {g["group"]: g["median"] for g in res["groups"]}
    assert medians["North"] > medians["Online"]


def test_kruskal_errors_on_missing_dv():
    study = _setup()
    try:
        q.kruskal(study, {"dv": "", "factor": "q7"})
        assert False, "expected ValueError for missing dv"
    except ValueError as e:
        assert "dependent variable" in str(e)


# --- wilcoxon ---------------------------------------------------------------------

def test_wilcoxon():
    study = _setup()
    res = q.wilcoxon(study, {"item_a": "q1", "item_b": "q4"})
    _assert_json(res)
    assert res["n_pairs"] == N
    assert res["W"] >= 0
    assert 0 <= res["p"] <= 1
    assert -1 <= res["rank_biserial"] <= 1
    assert res["median_a"] is not None and res["median_b"] is not None
    # Same item twice / unknown item are rejected.
    try:
        q.wilcoxon(study, {"item_a": "q1", "item_b": "q1"})
        assert False, "expected ValueError for identical items"
    except ValueError as e:
        assert "two different" in str(e)
    try:
        q.wilcoxon(study, {"item_a": "q1", "item_b": "nope"})
        assert False, "expected ValueError for unknown item"
    except ValueError as e:
        assert "nope" in str(e)


def test_wilcoxon_sign_matches_medians():
    study = _setup()
    res = q.wilcoxon(study, {"item_a": "q6", "item_b": "q1"})
    # rank-biserial r is positive when item_a tends to exceed item_b
    # (factor means roughly equal here, so only check internal consistency).
    res2 = q.wilcoxon(study, {"item_a": "q1", "item_b": "q6"})
    assert abs(res["rank_biserial"] + res2["rank_biserial"]) < 1e-6


# --- effect_sizes -----------------------------------------------------------------

def _expected_magnitude(value, cuts):
    v = abs(value)
    for cut, label in zip(cuts, ("negligible", "small", "medium")):
        if v < cut:
            return label
    return "large"


def test_effect_sizes_requires_completed_analyses():
    empty = _empty_study()
    try:
        q.effect_sizes(empty, {})
        assert False, "expected ValueError with no completed analyses"
    except ValueError as e:
        assert "No completed analyses" in str(e)


def test_effect_sizes_aggregates_and_labels():
    study = _setup()
    # Seed the analyses table through the real persistence path.
    q.run_analysis(study, "ttest", {"dv": "q1", "group_col": "q8"})
    q.run_analysis(study, "anova", {"dv": "q4", "factor": "q7"})
    q.run_analysis(study, "descriptives", {})  # carries no effect size

    res = q.effect_sizes(study, {})
    _assert_json(res)
    assert res["n_analyses"] >= 3
    by_kind = {}
    for e in res["effects"]:
        by_kind.setdefault(e["kind"], []).append(e)
        assert e["analysis_title"] and e["effect"]
        assert e["magnitude"] in ("negligible", "small", "medium", "large")

    # ttest -> Cohen's d, labeled on the .2/.5/.8 scale.
    (d_row,) = by_kind["ttest"]
    assert d_row["effect"] == "Cohen's d"
    assert d_row["magnitude"] == _expected_magnitude(d_row["value"], (0.2, 0.5, 0.8))
    assert abs(d_row["value"]) > 0.8 and d_row["magnitude"] == "large"

    # anova -> eta-squared, labeled on the .01/.06/.14 scale.
    (eta_row,) = by_kind["anova"]
    assert eta_row["effect"] == "eta-squared"
    assert eta_row["magnitude"] == _expected_magnitude(eta_row["value"], (0.01, 0.06, 0.14))

    # descriptives carried no effect size -> named in notes.
    assert any("descriptives" in n for n in res["notes"])


def test_effect_sizes_covers_correlation_pairs_and_more():
    study = _setup()
    q.run_analysis(study, "correlation", {"items": ["q1", "q2", "q4"]})
    q.run_analysis(study, "regression", {"dv": "q1", "ivs": ["q2", "q3"]})
    q.run_analysis(study, "crosstab", {"var1": "q7", "var2": "q8"})
    q.run_analysis(study, "mannwhitney", {"dv": "q1", "group_col": "q8"})
    q.run_analysis(study, "kruskal", {"dv": "q4", "factor": "q7"})

    res = q.effect_sizes(study, {})
    _assert_json(res)
    effects = {(e["kind"], e["effect"]): e for e in res["effects"]}
    # correlation -> one row per unique pair (3 items -> 3 pairs).
    corr_rows = [e for e in res["effects"] if e["kind"] == "correlation"]
    assert len(corr_rows) == 3
    pair = effects[("correlation", "r(q1, q2)")]
    assert pair["value"] > 0.5 and pair["magnitude"] == "large"
    # regression -> R² and adjusted R².
    assert ("regression", "R-squared") in effects
    assert ("regression", "Adjusted R-squared") in effects
    # crosstab -> Cramer's V on the .1/.3/.5 scale.
    cv = effects[("crosstab", "Cramer's V")]
    assert cv["magnitude"] == _expected_magnitude(cv["value"], (0.1, 0.3, 0.5))
    # mannwhitney / kruskal effect sizes present with correct labels.
    rb = effects[("mannwhitney", "rank-biserial r")]
    assert rb["magnitude"] == _expected_magnitude(rb["value"], (0.1, 0.3, 0.5))
    eps = effects[("kruskal", "epsilon-squared")]
    assert eps["magnitude"] == _expected_magnitude(eps["value"], (0.01, 0.06, 0.14))


# --- registry, persistence, rendering ----------------------------------------------

def test_new_kinds_registered_with_labels():
    for kind in ("efa", "manova", "mannwhitney", "kruskal", "wilcoxon",
                 "effect_sizes"):
        fn, label = q.ANALYSIS_KINDS[kind]
        assert callable(fn) and isinstance(label, str) and label


def test_param_specs_shape():
    expected_fields = {
        "efa": ["items", "n_factors"],
        "manova": ["dvs", "factor"],
        "mannwhitney": ["dv", "group_col"],
        "kruskal": ["dv", "factor"],
        "wilcoxon": ["item_a", "item_b"],
        "effect_sizes": [],
    }
    for kind, names in expected_fields.items():
        spec = q.PARAM_SPECS[kind]
        assert [f["name"] for f in spec["fields"]] == names
        for f in spec["fields"]:
            assert f["kind"] in ("numeric_select", "numeric_multiselect",
                                 "categorical_select", "text")
            assert isinstance(f["label"], str) and f["label"]
            assert isinstance(f["required"], bool)
        pff = spec["params_from_form"]
        assert set(pff) == {"multiselect_fields", "single_fields", "int_fields"}
        covered = set(pff["multiselect_fields"]) | set(pff["single_fields"])
        assert covered == set(names)
    assert q.PARAM_SPECS["efa"]["fields"][1]["label"] == "Number of factors (blank = auto)"
    json.dumps(q.PARAM_SPECS)  # machine-usable: plain JSON, no lambdas


def test_run_analysis_persists_new_kinds():
    study = _setup()
    analysis_id = q.run_analysis(study, "wilcoxon",
                                 {"item_a": "q1", "item_b": "q4"})
    row = db.get("analyses", analysis_id)
    assert row["kind"] == "wilcoxon" and row["status"] == "done"
    assert row["study_id"] == study["id"]
    assert row["results"]["interpretation"] is None  # no API key configured
    _assert_json(row["results"])


def test_result_tables_render_new_kinds():
    study = _setup()
    # effect_sizes needs prior analyses; earlier tests seeded plenty.
    for kind, params in [
        ("efa", {"items": ITEMS6}),
        ("manova", {"dvs": ["q1", "q4"], "factor": "q7"}),
        ("mannwhitney", {"dv": "q1", "group_col": "q8"}),
        ("kruskal", {"dv": "q4", "factor": "q7"}),
        ("wilcoxon", {"item_a": "q1", "item_b": "q4"}),
        ("effect_sizes", {}),
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
