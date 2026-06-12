"""Tests for the PDF article export, the OSF preregistration package and the
OSF push job. No real network: the Waterbutler HTTP layer is monkeypatched."""
import io
import json
import os
import sys
import tempfile
import urllib.error
import zipfile

# Point the app at a throwaway data dir BEFORE importing nbu_research.
os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp(prefix="nbu_test_pdf_osf_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nbu_research import create_app, db, jobs  # noqa: E402
from nbu_research.modules import exports  # noqa: E402
from nbu_research.modules import osf  # noqa: E402
from nbu_research.modules.exports.pdf import article_pdf  # noqa: E402
from nbu_research.modules.exports.prereg import project_prereg  # noqa: E402

db.init_db()
APP = create_app()

ARTICLE_MD = (
    "# Introduction\n\n"
    "This study — against the odds — examines “curly quotes” "
    "and an ellipsis…\n\n"
    "## Methods\n\n"
    "We did three things:\n\n"
    "- First with **bold** emphasis\n"
    "- Second with *italics*\n"
    "1. A numbered step\n\n"
    "### Detail\n\n"
    "Inline `code` too.\n\n"
    "```\nx <- lm(y ~ x)\nsummary(x)\n```\n\n"
    "---\n\n"
    "The end.\n"
)


def _setup():
    fix = {}
    fix["project"] = db.insert("projects", {
        "title": "Prereg project",
        "research_question": "Does flexibility drive engagement?",
        "description": "A test project for preregistration.",
        "methods_check_json": {
            "input": {"paradigm": "positivist"},
            "result": {
                "flags": [{"severity": "warning", "category": "power",
                           "explanation": "Planned subgroup comparisons may "
                                          "exceed the likely sample.",
                           "suggestion": "Run a power analysis first."}],
                "overall_assessment": "proceed_with_caution",
                "recommended_resources": [
                    {"title": "Field, Discovering Statistics",
                     "reason": "Power analysis chapter."}],
            },
            "checked_at": db.now(),
        },
    })
    fix["survey"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "survey",
        "title": "Engagement survey", "research_question": "RQ?",
        "config": {"questions": [
            {"id": "q1", "type": "likert", "text": "How engaged are you?",
             "required": True,
             "scale": {"min": 1, "max": 5,
                       "min_label": "Not at all", "max_label": "Very"}},
            {"id": "q2", "type": "multiple_choice", "text": "Work mode?",
             "options": ["Remote", "Office"]},
        ], "welcome_text": "Welcome"},
    })
    fix["interview"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "interview",
        "title": "Expert interviews",
        "config": {"interview_outline": "Ask about flexibility.",
                   "general_instructions": "Be kind."},
    })
    fix["codebook"] = db.insert("codebooks", {
        "study_id": fix["interview"], "name": "Round 1 codes",
        "codes": [{"id": "c1", "name": "Flexibility",
                   "description": "Mentions of flexible work", "parent_id": None}],
    })
    fix["analysis"] = db.insert("analyses", {
        "study_id": fix["survey"], "project_id": fix["project"],
        "kind": "descriptives", "title": "Baseline descriptives",
        "status": "done",
    })
    fix["review"] = db.insert("literature_reviews", {
        "project_id": fix["project"],
        "research_question": "What is known about engagement?",
        "status": "done",
    })
    db.insert("sources", {
        "review_id": fix["review"], "title": "Engagement at work",
        "authors": "Schaufeli, W.", "year": "2002", "venue": "JOHP",
    })
    fix["article"] = db.insert("articles", {
        "project_id": fix["project"], "title": "Engagement Article",
        "content_md": ARTICLE_MD,
    })
    fix["empty_article"] = db.insert("articles", {
        "project_id": fix["project"], "title": "Empty Draft", "content_md": "",
    })
    return fix


FIX = _setup()


# --- PDF export -----------------------------------------------------------------

def test_article_pdf_renders_markdown_and_unicode():
    data, filename, mimetype = article_pdf(FIX["article"])
    assert data.startswith(b"%PDF")
    assert len(data) > 1000
    assert filename == "engagement-article.pdf"
    assert mimetype == "application/pdf"


def test_article_pdf_empty_content_is_valid_pdf():
    data, filename, _ = article_pdf(FIX["empty_article"])
    assert data.startswith(b"%PDF")
    assert filename.endswith(".pdf")


def test_pdf_latin1_normalization():
    from nbu_research.modules.exports.pdf import _latin1
    assert _latin1("a — b… “q” ’  x") == \
        'a - b... "q" \'  x'
    assert _latin1("snow ☃ man") == "snow  man"  # non-latin-1 dropped


# --- preregistration package ------------------------------------------------------

def test_project_prereg_zip_contents():
    data, filename, mimetype = project_prereg(FIX["project"])
    assert data[:2] == b"PK"
    assert filename == "prereg-project-prereg.zip"
    assert mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "prereg.md" in names
    assert "README.md" in names
    instruments = [n for n in names if n.startswith("instruments/")]
    assert len(instruments) == 2  # survey + interview
    codebooks = [n for n in names if n.startswith("codebooks/")]
    assert len(codebooks) == 1

    prereg = zf.read("prereg.md").decode("utf-8")
    assert "Prereg project" in prereg
    assert "Does flexibility drive engagement?" in prereg
    assert "proceed_with_caution" in prereg
    assert "Run a power analysis first." in prereg          # methods flag
    assert "How engaged are you?" in prereg                  # survey question
    assert "likert" in prereg
    assert "Ask about flexibility." in prereg                # interview outline
    assert "analyses to date" in prereg                      # honest labeling
    assert "descriptives" in prereg
    assert "Schaufeli, W." in prereg                         # source citation

    cb = json.loads(zf.read(codebooks[0]).decode("utf-8"))
    assert cb["name"] == "Round 1 codes"
    assert cb["codes"][0]["name"] == "Flexibility"

    survey_md = zf.read([n for n in instruments if "engagement-survey" in n][0])
    assert b"How engaged are you?" in survey_md


def test_project_prereg_empty_project_does_not_crash():
    pid = db.insert("projects", {"title": "Bare project"})
    data, _, _ = project_prereg(pid)
    zf = zipfile.ZipFile(io.BytesIO(data))
    prereg = zf.read("prereg.md").decode("utf-8")
    assert "No analyses run to date" in prereg


# --- registry + routes -------------------------------------------------------------

def test_registry_has_new_exporters():
    for key in ("article_pdf", "project_prereg"):
        spec = exports.EXPORTERS[key]
        assert spec["applies_to"] in exports.TABLES
        assert callable(spec["fn"])
        assert spec["label"]
    assert exports.EXPORTERS["article_pdf"]["applies_to"] == "article"
    assert exports.EXPORTERS["project_prereg"]["applies_to"] == "project"
    assert exports.TABLES["project"] == "projects"


def test_project_export_routes():
    c = APP.test_client()
    r = c.get(f"/exports/project/{FIX['project']}")
    assert r.status_code == 200
    assert b"Preregistration package" in r.data
    r = c.get(f"/exports/project/{FIX['project']}/project_prereg")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"


def test_osf_index_page_renders_without_credential():
    c = APP.test_client()
    r = c.get("/osf/")
    assert r.status_code == 200
    assert b"not connected" in r.data


# --- OSF push job ------------------------------------------------------------------

class _FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_osf_push_job_success(monkeypatch):
    calls = {}

    def fake_urlopen(req, timeout=0, context=None):
        calls["url"] = req.full_url
        calls["auth"] = req.headers.get("Authorization")
        calls["body"] = req.data
        return _FakeResponse(json.dumps(
            {"data": {"attributes": {"name": "prereg-2026-06-12.zip"}}}
        ).encode("utf-8"))

    monkeypatch.setattr(osf.urllib.request, "urlopen", fake_urlopen)
    job_id = db.insert("jobs", {"kind": "osf_push", "status": "pending"})
    jobs.execute(job_id, "osf_push", {
        "project_id": FIX["project"], "osf_id": "ab12c", "token": "tok-123"})

    row = db.get("jobs", job_id)
    assert row["status"] == "done"
    assert row["result"]["osf_link"] == "https://osf.io/ab12c/files/"
    assert row["result"]["file_name"] == "prereg-2026-06-12.zip"
    assert "files.osf.io/v1/resources/ab12c/providers/osfstorage" in calls["url"]
    assert calls["auth"] == "Bearer tok-123"
    assert calls["body"][:2] == b"PK"


def test_osf_push_job_401_marks_job_error(monkeypatch):
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                     {}, io.BytesIO(b""))

    monkeypatch.setattr(osf.urllib.request, "urlopen", fake_urlopen)
    job_id = db.insert("jobs", {"kind": "osf_push", "status": "pending"})
    jobs.execute(job_id, "osf_push", {
        "project_id": FIX["project"], "osf_id": "ab12c", "token": "bad"})

    row = db.get("jobs", job_id)
    assert row["status"] == "error"
    assert "ValueError" in row["message"]
    assert "token" in row["message"]
    assert "bad" not in row["message"]  # the token value itself is not leaked
