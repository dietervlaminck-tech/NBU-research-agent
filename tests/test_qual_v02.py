"""Tests for the v0.2 qualitative analysis additions (deductive coding,
intercoder agreement, code co-occurrence).

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research so all
fixtures live in a throwaway SQLite database, and pops ANTHROPIC_API_KEY so
no live API call can happen. LLM-dependent jobs are exercised by
monkeypatching llm.complete_json / llm.complete with canned outputs.
"""
import os
import sys
import tempfile

# Make the project root importable regardless of how the tests are invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate the database and make sure no API key leaks in -- BEFORE import.
_TMP = tempfile.mkdtemp(prefix="nbu_test_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db, jobs, llm  # noqa: E402
from nbu_research.modules.analysis import qualitative as ql  # noqa: E402

_state = {}


# --------------------------------------------------------------------------
# cohens_kappa (pure, no DB / no API)
# --------------------------------------------------------------------------

def test_kappa_perfect_agreement():
    assert ql.cohens_kappa([1, 0, 1, 0, 1], [1, 0, 1, 0, 1]) == 1.0


def test_kappa_textbook_example():
    # Classic 2x2 table: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no (n=50).
    # po = 0.70, pe = 0.5*0.6 + 0.5*0.4 = 0.50 -> kappa = 0.40.
    a = [1] * 20 + [1] * 5 + [0] * 10 + [0] * 15
    b = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15
    k = ql.cohens_kappa(a, b)
    assert abs(k - 0.40) < 1e-9


def test_kappa_degenerate_all_same():
    assert ql.cohens_kappa([1, 1, 1], [1, 1, 1]) is None  # pe == 1
    assert ql.cohens_kappa([0, 0, 0, 0], [0, 0, 0, 0]) is None
    assert ql.cohens_kappa([], []) is None


def test_kappa_total_disagreement_and_errors():
    # Coder A all-yes vs Coder B all-no: pe = 0, po = 0 -> kappa = 0.0.
    assert ql.cohens_kappa([1, 1, 1], [0, 0, 0]) == 0.0
    try:
        ql.cohens_kappa([1, 0], [1])
        assert False, "expected ValueError for unequal lengths"
    except ValueError as e:
        assert "equal length" in str(e)


# --------------------------------------------------------------------------
# parse_codebook_upload (pure, no DB / no API)
# --------------------------------------------------------------------------

def test_parse_codebook_json():
    text = """[
      {"id": "costs", "name": "Costs", "description": "money", "parent_id": null},
      {"name": "Time Pressure!", "description": "", "parent_id": "costs"},
      {"name": "Orphan", "parent_id": "nonexistent"}
    ]"""
    codes = ql.parse_codebook_upload(text)
    assert [c["id"] for c in codes] == ["costs", "time_pressure", "orphan"]
    assert codes[0]["description"] == "money"
    assert codes[1]["parent_id"] == "costs"      # resolved parent kept
    assert codes[2]["parent_id"] is None         # dangling parent dropped
    for c in codes:
        assert set(c) == {"id", "name", "description", "parent_id"}


def test_parse_codebook_json_codes_wrapper():
    codes = ql.parse_codebook_upload('{"codes": [{"name": "Alpha"}]}')
    assert codes == [{"id": "alpha", "name": "Alpha", "description": "",
                      "parent_id": None}]


def test_parse_codebook_csv():
    text = ("id,name,description,parent_id\n"
            "costs,Costs,money spent,\n"
            ",Time Pressure,deadlines,costs\n")
    codes = ql.parse_codebook_upload(text)
    assert [c["id"] for c in codes] == ["costs", "time_pressure"]
    assert codes[0]["description"] == "money spent"
    assert codes[0]["parent_id"] is None
    assert codes[1]["parent_id"] == "costs"


def test_parse_codebook_dedups_ids():
    codes = ql.parse_codebook_upload(
        '[{"id": "x", "name": "One"}, {"id": "x", "name": "Two"}, {"name": "x"}]')
    assert [c["id"] for c in codes] == ["x", "x_2", "x_3"]


def test_parse_codebook_malformed():
    cases = [
        "",                                   # empty
        "[{broken json",                      # unparseable JSON
        '{"not_codes": 1}',                   # JSON but not a list / codes
        '[{"description": "no name"}]',       # empty name (JSON)
        "id,description\na,b\n",              # CSV without name header
        'name,description\n"",empty\n',       # empty name (CSV)
    ]
    for text in cases:
        try:
            ql.parse_codebook_upload(text)
            assert False, f"expected ValueError for {text!r}"
        except ValueError:
            pass


# --------------------------------------------------------------------------
# Fixtures for the DB-backed tests
# --------------------------------------------------------------------------

CODES_3 = [
    {"id": "c1", "name": "Costs", "description": "money", "parent_id": None},
    {"id": "c2", "name": "Benefits", "description": "gains", "parent_id": None},
    {"id": "c3", "name": "Risks", "description": "dangers", "parent_id": None},
]


def _make_study(title, n_sessions):
    """Insert a project + interview study with n completed sessions."""
    db.init_db()
    project_id = db.insert("projects", {"title": f"Fixture project — {title}"})
    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "interview",
        "title": title,
        "research_question": "How do teachers adopt AI?",
        "config": {"interview_outline": "...", "general_instructions": ""},
    })
    session_ids = []
    for i in range(n_sessions):
        session_ids.append(db.insert("sessions", {
            "study_id": study_id,
            "respondent_name": f"r{i}",
            "messages": [
                {"role": "assistant", "content": "How do you use AI?"},
                {"role": "user", "content": f"Respondent {i} talks about costs "
                                            "and benefits of AI in teaching."},
            ],
            "status": "completed",
            "started_at": db.now(),
            "completed_at": db.now(),
        }))
    return project_id, study_id, session_ids


def _patched_llm(complete_json_fn, complete_fn):
    """Return (apply, restore) for monkeypatching the shared llm module."""
    originals = (llm.complete_json, llm.complete)

    def apply():
        llm.complete_json = complete_json_fn
        llm.complete = complete_fn

    def restore():
        llm.complete_json, llm.complete = originals

    return apply, restore


def _run_registered_job(kind, payload):
    """Create a job row and run the registered function via jobs.execute."""
    job_id = db.insert("jobs", {"kind": kind, "status": "pending",
                                "message": "Queued"})
    jobs.execute(job_id, kind, payload)
    return db.get("jobs", job_id)


# --------------------------------------------------------------------------
# cooccurrence (pure Python over fixture coded_segments)
# --------------------------------------------------------------------------

def test_cooccurrence_matrix():
    _, study_id, sids = _make_study("Co-occurrence fixture", 4)
    codebook_id = db.insert("codebooks", {
        "study_id": study_id, "name": "CB", "codes": CODES_3,
    })
    # s0: c1,c2 | s1: c1,c2,c3 | s2: c1,c2 (c1 twice -> presence counted once)
    # s3: c3
    plan = [(sids[0], ["c1", "c2"]),
            (sids[1], ["c1", "c2", "c3"]),
            (sids[2], ["c1", "c1", "c2"]),
            (sids[3], ["c3"])]
    for sid, code_list in plan:
        for code_id in code_list:
            db.insert("coded_segments", {
                "codebook_id": codebook_id, "session_id": sid,
                "code_id": code_id, "text": "q", "memo": "",
            })
    study = db.get("studies", study_id)
    res = ql.cooccurrence(study, {"codebook_id": codebook_id})
    assert [c["id"] for c in res["codes"]] == ["c1", "c2", "c3"]
    assert res["n_sessions"] == 4
    assert res["matrix"] == [[3, 3, 1],
                             [3, 3, 1],
                             [1, 1, 2]]
    assert res["pairs"][0] == {"code_a": "c1", "code_b": "c2", "count": 3}
    assert len(res["pairs"]) == 3
    counts = sorted(p["count"] for p in res["pairs"])
    assert counts == [1, 1, 3]


def test_cooccurrence_errors_without_segments():
    _, study_id, _ = _make_study("Empty codebook fixture", 1)
    codebook_id = db.insert("codebooks", {
        "study_id": study_id, "name": "Empty CB", "codes": CODES_3,
    })
    study = db.get("studies", study_id)
    try:
        ql.cooccurrence(study, {"codebook_id": codebook_id})
        assert False, "expected ValueError for codebook without segments"
    except ValueError as e:
        assert "no coded segments" in str(e)


# --------------------------------------------------------------------------
# Deductive job (LLM monkeypatched, registered function run via jobs.execute)
# --------------------------------------------------------------------------

def _deductive_fixture():
    """Run the deductive job once against canned LLM output (idempotent)."""
    if _state.get("deductive"):
        return _state["deductive"]
    project_id, study_id, sids = _make_study("Deductive fixture", 2)
    codebook_id = db.insert("codebooks", {
        "study_id": study_id, "name": "Imported CB", "codes": CODES_3[:2],
    })

    def fake_complete_json(system, prompt, schema, **kw):
        return {"segments": [
            {"code_id": "c1", "text": "costs quote", "memo": "m1"},
            {"code_id": "bogus", "text": "must be dropped", "memo": ""},
            {"code_id": "c2", "text": "benefits quote", "memo": "m2"},
        ]}

    def fake_complete(system, prompt, **kw):
        return "DEDUCTIVE REPORT"

    apply, restore = _patched_llm(fake_complete_json, fake_complete)
    apply()
    try:
        job = _run_registered_job(
            "deductive", {"study_id": study_id, "codebook_id": codebook_id})
    finally:
        restore()
    _state["deductive"] = {
        "project_id": project_id, "study_id": study_id, "session_ids": sids,
        "codebook_id": codebook_id, "job": job,
    }
    return _state["deductive"]


def test_deductive_job_inserts_segments_and_analysis():
    fx = _deductive_fixture()
    job = fx["job"]
    assert job["status"] == "done", job["message"]
    analysis_id = job["result"]["analysis_id"]

    # Segments land under the SAME pre-existing codebook; unknown ids dropped.
    segments = db.query("coded_segments", "codebook_id = ?", (fx["codebook_id"],))
    assert len(segments) == 4  # 2 sessions x 2 valid canned segments
    assert {s["code_id"] for s in segments} == {"c1", "c2"}
    assert {s["session_id"] for s in segments} == set(fx["session_ids"])
    assert all(s["codebook_id"] == fx["codebook_id"] for s in segments)

    row = db.get("analyses", analysis_id)
    assert row["kind"] == "deductive" and row["status"] == "done"
    assert row["study_id"] == fx["study_id"]
    res = row["results"]
    assert res["report_md"] == "DEDUCTIVE REPORT"
    assert res["codebook_id"] == fx["codebook_id"]
    assert res["n_sessions"] == 2 and res["n_segments"] == 4
    counts = {c["code_id"]: c["count"] for c in res["code_counts"]}
    assert counts == {"c1": 2, "c2": 2}


def test_deductive_job_marks_error_on_missing_codebook():
    _, study_id, _ = _make_study("Deductive error fixture", 1)
    job = _run_registered_job(
        "deductive", {"study_id": study_id, "codebook_id": "nope"})
    assert job["status"] == "error"
    assert "Codebook not found" in job["message"]


# --------------------------------------------------------------------------
# Intercoder job (LLM monkeypatched; must NOT insert coded_segments)
# --------------------------------------------------------------------------

def test_intercoder_job():
    fx = _deductive_fixture()
    base_analysis_id = fx["job"]["result"]["analysis_id"]
    n_segments_before = len(db.query("coded_segments", "codebook_id = ?",
                                     (fx["codebook_id"],)))
    seen_systems = []

    def fake_complete_json(system, prompt, schema, **kw):
        seen_systems.append(system)
        # Coder B finds only c1 in every session (Coder A had c1 AND c2).
        return {"segments": [{"code_id": "c1", "text": "q", "memo": ""}]}

    def fake_complete(system, prompt, **kw):
        return "INTERCODER REPORT"

    apply, restore = _patched_llm(fake_complete_json, fake_complete)
    apply()
    try:
        job = _run_registered_job(
            "intercoder",
            {"study_id": fx["study_id"], "analysis_id": base_analysis_id})
    finally:
        restore()
    assert job["status"] == "done", job["message"]

    # The reliability probe is in-memory only — no new coded_segments.
    n_segments_after = len(db.query("coded_segments", "codebook_id = ?",
                                    (fx["codebook_id"],)))
    assert n_segments_after == n_segments_before

    # Coder B really got the independent-coder system prompt.
    assert seen_systems and all("Coder B" in s for s in seen_systems)

    row = db.get("analyses", job["result"]["analysis_id"])
    assert row["kind"] == "intercoder" and row["status"] == "done"
    res = row["results"]
    assert res["report_md"] == "INTERCODER REPORT"
    assert res["codebook_id"] == fx["codebook_id"]
    assert res["base_analysis_id"] == base_analysis_id
    assert res["n_sessions"] == 2

    per_code = {k["code_id"]: k for k in res["kappa_per_code"]}
    assert set(per_code) == {"c1", "c2"}
    # c1: both coders present in both sessions -> degenerate, kappa undefined.
    assert per_code["c1"]["kappa"] is None
    assert per_code["c1"]["percent_agreement"] == 1.0
    # c2: A present in both, B in neither -> total disagreement, kappa 0.0.
    assert per_code["c2"]["kappa"] == 0.0
    assert per_code["c2"]["percent_agreement"] == 0.0
    assert all(k["n"] == 2 and k["name"] for k in res["kappa_per_code"])
    assert res["kappa_mean"] == 0.0  # mean over defined kappas only


def test_intercoder_rejects_wrong_base_kind():
    fx = _deductive_fixture()
    quant_id = db.insert("analyses", {
        "kind": "ttest", "study_id": fx["study_id"], "title": "t",
        "params": {}, "results": {}, "status": "done",
    })
    job = _run_registered_job(
        "intercoder", {"study_id": fx["study_id"], "analysis_id": quant_id})
    assert job["status"] == "error"
    assert "thematic or deductive" in job["message"]


# --------------------------------------------------------------------------
# Public surface the integrator wires
# --------------------------------------------------------------------------

def test_public_surface():
    assert "deductive" in jobs.REGISTRY and "intercoder" in jobs.REGISTRY
    assert callable(ql.start_deductive_job) and callable(ql.start_intercoder_job)
    fn, label = ql.SYNC_KINDS["cooccurrence"]
    assert fn is ql.cooccurrence and label == "Code co-occurrence"


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
