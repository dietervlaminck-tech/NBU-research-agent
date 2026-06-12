"""Tests for the mixed-methods integration analysis (LLM mocked)."""
import os
import tempfile

os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db  # noqa: E402
db.init_db()

from nbu_research.modules.analysis import mixed, quantitative  # noqa: E402
from nbu_research import jobs, llm  # noqa: E402

PROJ = db.insert("projects", {"title": "MM fixture"})

# Qualitative side: interview study + codebook + done thematic analysis.
INT = db.insert("studies", {"project_id": PROJ, "study_type": "interview",
                            "title": "Interviews", "config": {}})
CB = db.insert("codebooks", {"study_id": INT, "name": "cb", "codes": [
    {"id": "trust", "name": "Trust", "description": "", "parent_id": None},
    {"id": "stress", "name": "Stress", "description": "", "parent_id": None},
]})
QUAL = db.insert("analyses", {
    "study_id": INT, "project_id": PROJ, "kind": "thematic", "title": "Them",
    "status": "done",
    "results": {"report_md": "# Themes\nTrust grows; stress varies.",
                "codebook_id": CB,
                "code_counts": [{"code_id": "trust", "name": "Trust", "count": 5},
                                 {"code_id": "stress", "name": "Stress", "count": 3}]},
})

# Quantitative side: survey with construct-tagged items + responses.
SUR = db.insert("studies", {"project_id": PROJ, "study_type": "survey",
                            "title": "Survey", "config": {"questions": [
    {"id": "t1", "type": "likert", "text": "I trust my team", "construct": "trust_scale",
     "scale": {"min": 1, "max": 5}},
    {"id": "t2", "type": "likert", "text": "Trust is high", "construct": "trust_scale",
     "scale": {"min": 1, "max": 5}},
    {"id": "s1", "type": "likert", "text": "I feel stressed", "construct": "stress_scale",
     "scale": {"min": 1, "max": 5}},
]}})
for i in range(8):
    db.insert("survey_responses", {
        "study_id": SUR, "respondent_name": f"R{i}", "status": "completed",
        "answers": {"t1": 4, "t2": 5, "s1": 2 + (i % 3)},
        "started_at": db.now(),
    })


def test_themes_and_constructs_extraction():
    themes = mixed._themes_from(db.get("analyses", QUAL))
    assert {t["name"] for t in themes} == {"Trust", "Stress"}
    cons = mixed._constructs_from(db.get("studies", SUR))
    assert {c["name"] for c in cons} == {"trust_scale", "stress_scale"}
    stats = mixed._quant_summary(db.get("studies", SUR), cons)
    assert all(s["n"] == 8 for s in stats)
    trust = next(s for s in stats if s["construct"] == "trust_scale")
    assert trust["mean"] and 4.0 <= trust["mean"] <= 5.0


def test_mixed_job_stores_analysis(monkeypatch):
    canned_cells = {"cells": [
        {"theme": "Trust", "construct": "trust_scale", "relation": "converges",
         "note": "Both strands show high trust."},
        {"theme": "Stress", "construct": "stress_scale", "relation": "complements",
         "note": "Interviews explain the variance."},
    ]}
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: canned_cells)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "## Meta-inferences\nConverges.")
    job_id = db.insert("jobs", {"kind": "mixed_methods", "status": "running"})
    result = mixed._run_mixed_job(
        job_id, project_id=PROJ, thematic_analysis_id=QUAL,
        quant_kind="study", quant_id=SUR)
    row = db.get("analyses", result["analysis_id"])
    assert row["kind"] == "mixed_methods" and row["status"] == "done"
    assert row["project_id"] == PROJ
    assert len(row["results"]["cells"]) == 2
    assert row["results"]["constructs"] == ["trust_scale", "stress_scale"] or \
        set(row["results"]["constructs"]) == {"trust_scale", "stress_scale"}


def test_mixed_job_validates_inputs():
    job_id = db.insert("jobs", {"kind": "mixed_methods", "status": "running"})
    try:
        mixed._run_mixed_job(job_id, project_id=PROJ,
                             thematic_analysis_id="nope",
                             quant_kind="study", quant_id=SUR)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "completed" in str(e)


if __name__ == "__main__":
    import types
    mp = types.SimpleNamespace(setattr=lambda o, n, v: setattr(o, n, v))
    test_themes_and_constructs_extraction()
    test_mixed_job_stores_analysis(mp)
    test_mixed_job_validates_inputs()
    print("all mixed tests passed")
