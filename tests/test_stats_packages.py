"""Tests for Stata/R/Python exports and replication packages."""
import json
import os
import tempfile
import zipfile
import io

os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db  # noqa: E402

db.init_db()

QUESTIONS = [
    {"id": "q1", "type": "likert", "text": "I trust my team.", "required": True,
     "scale": {"min": 1, "max": 5, "min_label": "Disagree", "max_label": "Agree"}},
    {"id": "q2", "type": "likert", "text": "We communicate openly.", "required": True,
     "scale": {"min": 1, "max": 5, "min_label": "Disagree", "max_label": "Agree"}},
    {"id": "q3", "type": "multiple_choice", "text": "Work mode", "required": True,
     "options": ["Remote", "Hybrid", "On-site"]},
    {"id": "q4", "type": "numeric", "text": "Office days", "required": True,
     "scale": {"min": 0, "max": 7}},
]

STUDY_ID = db.insert("studies", {
    "study_type": "survey", "title": "Stats package fixture",
    "research_question": "RQ", "config": {"questions": QUESTIONS},
})
for i in range(12):
    db.insert("survey_responses", {
        "study_id": STUDY_ID, "respondent_name": f"R{i}",
        "answers": {"q1": (i % 5) + 1, "q2": ((i + 1) % 5) + 1,
                    "q3": ["Remote", "Hybrid", "On-site"][i % 3], "q4": i % 6},
        "status": "completed", "started_at": db.now(),
    })

from nbu_research.modules.exports import stats_packages, EXPORTERS, _formats_for  # noqa: E402
from nbu_research.modules.analysis import quantitative  # noqa: E402

ANALYSIS_ID = quantitative.run_analysis(
    db.get("studies", STUDY_ID), "regression", {"dv": "q1", "ivs": ["q2", "q4"]})
THEMATIC_ID = db.insert("analyses", {
    "study_id": STUDY_ID, "kind": "thematic", "title": "Them", "status": "done",
})


def test_study_dta():
    data, name, _ = stats_packages.study_dta(STUDY_ID)
    assert name.endswith(".dta") and len(data) > 200
    import pyreadstat
    fd, path = tempfile.mkstemp(suffix=".dta"); os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    df, meta = pyreadstat.read_dta(path)
    os.remove(path)
    assert len(df) == 12
    assert all(len(c) <= 32 for c in df.columns)
    assert "I trust my team." in " ".join(meta.column_labels)


def test_study_rds():
    data, name, _ = stats_packages.study_rds(STUDY_ID)
    assert name.endswith(".rds") and len(data) > 50
    # Read back in a subprocess: pyreadr's librdata clashes with pyreadstat's
    # readstat in-process (the same reason study_rds itself uses a subprocess).
    import subprocess, sys
    fd, path = tempfile.mkstemp(suffix=".rds"); os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    script = ("import sys, pyreadr; r = pyreadr.read_r(sys.argv[1]); "
              "print(len(list(r.values())[0]))")
    proc = subprocess.run([sys.executable, "-c", script, path],
                          capture_output=True, text=True, timeout=120)
    os.remove(path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "12"


def test_study_ipynb():
    data, name, _ = stats_packages.study_ipynb(STUDY_ID)
    nb = json.loads(data)
    assert nb["nbformat"] == 4
    src = json.dumps(nb)
    assert "read_csv" in src and "q1" in src


def test_analysis_scripts():
    for fn, marker in [(stats_packages.analysis_do, "regress q1 q2 q4"),
                       (stats_packages.analysis_r, "lm(q1 ~ q2 + q4"),
                       (stats_packages.analysis_py, "smf.ols('q1 ~ q2 + q4'")]:
        data, _name, _ = fn(ANALYSIS_ID)
        assert marker in data.decode("utf-8")


def test_analysis_zip():
    data, name, _ = stats_packages.analysis_zip(ANALYSIS_ID)
    assert data[:2] == b"PK"
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert {"data.csv", "analysis.do", "analysis.R", "analysis.py",
            "codebook.csv", "results.json", "README.md"} <= names
    csv = z.read("data.csv").decode("utf-8")
    assert csv.count("\n") >= 12 and "q1" in csv


def test_kind_filtering():
    quant = _formats_for("analysis", db.get("analyses", ANALYSIS_ID))
    them = _formats_for("analysis", db.get("analyses", THEMATIC_ID))
    assert any(k == "analysis_zip" for k, _ in quant)
    assert not them  # no replication exports for thematic


def test_python_script_executes():
    """The generated Python script must actually run against data.csv."""
    import subprocess, sys
    data, _n, _ = stats_packages.analysis_zip(ANALYSIS_ID)
    workdir = tempfile.mkdtemp()
    zipfile.ZipFile(io.BytesIO(data)).extractall(workdir)
    proc = subprocess.run([sys.executable, "analysis.py"], cwd=workdir,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "OLS" in proc.stdout


if __name__ == "__main__":
    for fn_name in sorted(k for k in dir() if k.startswith("test_")):
        globals()[fn_name]()
        print(f"{fn_name} OK")
    print("all tests passed")
