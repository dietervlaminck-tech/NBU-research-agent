"""Tests for the exports module: every exporter returns non-empty bytes with
the right magic bytes, against minimal fixture rows in a temp database."""
import json
import os
import sys
import tempfile
import zipfile
import io

# Point the app at a throwaway data dir BEFORE importing nbu_research.
os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp(prefix="nbu_test_exports_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nbu_research import db  # noqa: E402
from nbu_research.modules import exports  # noqa: E402
from nbu_research.modules.exports.spss import sanitize_varnames  # noqa: E402

db.init_db()

PK = b"PK\x03\x04"

SURVEY_QUESTIONS = [
    {"id": "q1", "type": "likert", "text": "How satisfied are you?",
     "required": True,
     "scale": {"min": 1, "max": 5, "min_label": "Not at all", "max_label": "Very"}},
    {"id": "q2", "type": "multiple_choice", "text": "Favourite colour?",
     "required": False, "options": ["Red", "Blue"]},
    {"id": "q3", "type": "checkbox", "text": "Devices used?",
     "required": False, "options": ["Phone", "Laptop", "Tablet"]},
    {"id": "q4", "type": "open", "text": "Any comments?", "required": False},
    {"id": "q5", "type": "numeric", "text": "Your age?", "required": False,
     "scale": {"min": 18, "max": 99}},
    {"id": "q6", "type": "matrix", "text": "Rate each aspect",
     "required": False, "rows": ["Speed", "Quality"],
     "scale": {"min": 1, "max": 5, "min_label": "Poor", "max_label": "Great"}},
    {"id": "q7", "type": "dropdown", "text": "Country?", "required": False,
     "options": ["NL", "BE"]},
]


def _setup():
    fix = {}
    fix["project"] = db.insert("projects", {"title": "Test project"})

    # Survey study with two responses (one partial).
    fix["survey"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "survey",
        "title": "Customer survey", "research_question": "RQ?",
        "config": {"questions": SURVEY_QUESTIONS,
                   "welcome_text": "Welcome", "thankyou_text": "Thanks"},
    })
    db.insert("survey_responses", {
        "study_id": fix["survey"], "respondent_name": "Alice",
        "answers": {"q1": 4, "q2": "Blue", "q3": ["Phone", "Laptop"],
                    "q4": "Great service", "q5": 34,
                    "q6": {"Speed": 5, "Quality": 4}, "q7": "NL"},
        "status": "completed", "started_at": db.now(), "completed_at": db.now(),
    })
    db.insert("survey_responses", {
        "study_id": fix["survey"], "respondent_name": "Bob",
        "answers": {"q1": 2}, "status": "started", "started_at": db.now(),
    })

    # Interview study with sessions, a codebook and coded segments.
    fix["interview"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "interview",
        "title": "Expert interviews", "research_question": "Why?",
        "config": {"interview_outline": "Outline", "general_instructions": ""},
    })
    fix["session1"] = db.insert("sessions", {
        "study_id": fix["interview"], "respondent_name": "Carol",
        "messages": [
            {"role": "assistant", "content": "How do you experience remote work?"},
            {"role": "user", "content": "I really enjoy the flexibility it gives me."},
            {"role": "assistant", "content": "Can you tell me more?"},
            {"role": "user", "content": "Sure, the commute time savings are huge."},
        ],
        "status": "completed", "started_at": db.now(), "completed_at": db.now(),
    })
    fix["session2"] = db.insert("sessions", {
        "study_id": fix["interview"], "respondent_name": "",
        "messages": [{"role": "assistant", "content": "Hello?"}],
        "status": "active", "started_at": db.now(),
    })
    fix["codebook"] = db.insert("codebooks", {
        "study_id": fix["interview"], "name": "CB1",
        "codes": [
            {"id": "c1", "name": "Flexibility", "description": "Mentions of flexibility",
             "parent_id": None},
            {"id": "c2", "name": "Commute", "description": "", "parent_id": "c1"},
        ],
    })
    db.insert("coded_segments", {
        "codebook_id": fix["codebook"], "session_id": fix["session1"],
        "code_id": "c1", "text": "enjoy the flexibility",
    })
    db.insert("coded_segments", {
        "codebook_id": fix["codebook"], "session_id": fix["session1"],
        "code_id": "c2", "text": "THIS TEXT DOES NOT OCCUR ANYWHERE",
    })

    # Empty studies (no responses / sessions / questions).
    fix["empty_survey"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "survey",
        "title": "Empty survey", "config": {"questions": []},
    })
    fix["empty_interview"] = db.insert("studies", {
        "project_id": fix["project"], "study_type": "interview",
        "title": "Empty interviews", "config": {},
    })

    # Article.
    fix["article"] = db.insert("articles", {
        "project_id": fix["project"], "title": "My Article",
        "content_md": ("# Introduction\n\nSome **bold** and *italic* text "
                       "with 100% & special_chars.\n\n## Methods\n\n"
                       "- item one\n- item two\n\n1. first\n2. second\n"),
        "metadata": {"author": "D. Vlaminck"},
    })
    fix["empty_article"] = db.insert("articles", {
        "project_id": fix["project"], "title": "Empty Article",
    })

    # Literature review with sources (same author+year to test key dedup).
    fix["review"] = db.insert("literature_reviews", {
        "project_id": fix["project"], "research_question": "What is known?",
        "report_md": "# Review\n\nFindings with **emphasis**.\n",
        "status": "completed",
    })
    db.insert("sources", {
        "review_id": fix["review"], "title": "First paper",
        "authors": "Jane Smith; John Doe", "year": "2020",
        "venue": "Journal of Tests", "doi": "10.1/abc", "url": "https://x.test/1",
    })
    db.insert("sources", {
        "review_id": fix["review"], "title": "Second paper",
        "authors": "Jane Smith", "year": "2020", "url": "https://x.test/2",
    })
    fix["empty_review"] = db.insert("literature_reviews", {
        "project_id": fix["project"], "research_question": "Empty?",
    })
    return fix


FIX = _setup()


def _export(key, obj_id):
    spec = exports.EXPORTERS[key]
    data, filename, mimetype = spec["fn"](obj_id)
    assert isinstance(data, bytes), f"{key}: not bytes"
    assert data, f"{key}: empty bytes"
    assert filename and "." in filename, f"{key}: bad filename {filename!r}"
    assert mimetype, f"{key}: missing mimetype"
    return data


# --- registry -------------------------------------------------------------------

def test_registry_shape():
    assert exports.EXPORTERS
    for key, spec in exports.EXPORTERS.items():
        assert spec["applies_to"] in ("study", "article", "review"), key
        assert spec["label"], key
        assert callable(spec["fn"]), key


# --- study: json / csv / xlsx -----------------------------------------------------

def test_study_json():
    data = _export("study_json", FIX["survey"])
    assert data.lstrip().startswith(b"{")
    dump = json.loads(data)
    assert dump["study"]["id"] == FIX["survey"]
    assert len(dump["survey_responses"]) == 2
    data = _export("study_json", FIX["interview"])
    dump = json.loads(data)
    assert len(dump["sessions"]) == 2
    assert dump["coded_segments"]


def test_survey_csv_wide_matrix():
    data = _export("study_csv", FIX["survey"])
    text = data.decode("utf-8")
    header = text.splitlines()[0]
    assert "respondent_name" in header
    assert "q1" in header
    assert "q6_Speed" in header and "q6_Quality" in header  # matrix flattened
    assert "Phone;Laptop" in text  # checkbox joined with ';'


def test_interview_csv_transcripts():
    data = _export("study_csv", FIX["interview"])
    text = data.decode("utf-8")
    assert "transcript" in text.splitlines()[0]
    assert "Interviewer: How do you experience remote work?" in text
    assert "Respondent: I really enjoy the flexibility" in text


def test_study_xlsx():
    for sid in (FIX["survey"], FIX["interview"]):
        assert _export("study_xlsx", sid).startswith(PK)


# --- study: sav -------------------------------------------------------------------

def test_sanitize_varnames():
    names = sanitize_varnames(["q1", "q1", "1weird id!", "_x", "a" * 100])
    assert names[0] == "q1" and names[1] != "q1"
    assert names[2][0].isalpha()
    for n in names:
        assert len(n.encode()) <= 64
        assert n[0].isalpha()
        assert all(c.isalnum() or c == "_" for c in n)
    assert len(set(n.lower() for n in names)) == len(names)


def test_survey_sav():
    import pyreadstat
    data = _export("study_sav", FIX["survey"])
    assert data.startswith(b"$FL2")  # SPSS .sav magic
    fd, path = tempfile.mkstemp(suffix=".sav")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        df, meta = pyreadstat.read_sav(path)
        assert len(df) == 2
        assert "q1" in df.columns
        assert float(df["q1"].iloc[0]) == 4.0
        labels = meta.variable_value_labels.get("q1", {})
        assert "Not at all" in labels.values() and "Very" in labels.values()
        assert "How satisfied are you?" in meta.column_names_to_labels.values()
    finally:
        os.remove(path)


# --- study: docx / md transcripts ---------------------------------------------------

def test_interview_docx():
    assert _export("study_docx", FIX["interview"]).startswith(PK)


def test_interview_md():
    data = _export("study_md", FIX["interview"])
    text = data.decode("utf-8")
    assert text.startswith("# Expert interviews")
    assert "**Interviewer:**" in text and "**Respondent:**" in text


# --- study: qdpx --------------------------------------------------------------------

def test_interview_qdpx():
    data = _export("study_qdpx", FIX["interview"])
    assert data.startswith(PK)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "project.qde" in names
    assert sum(1 for n in names if n.startswith("sources/") and n.endswith(".txt")) == 2
    xml = zf.read("project.qde").decode("utf-8")
    assert 'urn:QDA-XML:project:1.0' in xml
    assert "Flexibility" in xml and "Commute" in xml
    assert "PlainTextSelection" in xml and "CodeRef" in xml
    # The not-found segment was skipped: only one selection embedded.
    assert xml.count("<PlainTextSelection") == 1
    # Coded offsets point at the real segment text in the source file.
    src = next(n for n in names if n.startswith("sources/"))
    import re
    start = int(re.search(r'startPosition="(\d+)"', xml).group(1))
    end = int(re.search(r'endPosition="(\d+)"', xml).group(1))
    guid = re.search(r'plainTextPath="internal://([^"]+)\.txt"', xml)
    assert guid
    for n in names:
        if n.startswith("sources/"):
            text = zf.read(n).decode("utf-8")
            if "enjoy the flexibility" in text:
                assert text[start:end] == "enjoy the flexibility"


# --- study: qsf --------------------------------------------------------------------

def test_survey_qsf():
    data = _export("study_qsf", FIX["survey"])
    assert data.lstrip().startswith(b"{")
    qsf = json.loads(data)
    assert qsf["SurveyEntry"]["SurveyName"] == "Customer survey"
    elements = qsf["SurveyElements"]
    kinds = [e["Element"] for e in elements]
    for required in ("BL", "FL", "SO", "SQ"):
        assert required in kinds
    sq = [e for e in elements if e["Element"] == "SQ"]
    assert len(sq) == len(SURVEY_QUESTIONS)
    by_tag = {e["Payload"]["DataExportTag"]: e["Payload"] for e in sq}
    assert by_tag["q1"]["QuestionType"] == "MC"
    assert by_tag["q1"]["Selector"] == "SAVR"
    assert by_tag["q3"]["Selector"] == "MAVR"
    assert by_tag["q4"]["QuestionType"] == "TE"
    assert by_tag["q5"]["Validation"]["Settings"].get("ContentType") == "ValidNumber"
    assert by_tag["q6"]["QuestionType"] == "Matrix"
    assert by_tag["q7"]["Selector"] == "DL"
    block = next(e for e in elements if e["Element"] == "BL")
    assert len(block["Payload"][0]["BlockElements"]) == len(SURVEY_QUESTIONS)


# --- study type filtering -------------------------------------------------------------

def test_format_filtering_by_study_type():
    survey_keys = {k for k, s in exports.EXPORTERS.items()
                   if s["applies_to"] == "study"
                   and (not s.get("study_types") or "survey" in s["study_types"])}
    interview_keys = {k for k, s in exports.EXPORTERS.items()
                      if s["applies_to"] == "study"
                      and (not s.get("study_types") or "interview" in s["study_types"])}
    assert "study_sav" in survey_keys and "study_sav" not in interview_keys
    assert "study_qdpx" in interview_keys and "study_qdpx" not in survey_keys


# --- articles -------------------------------------------------------------------------

def test_article_md():
    data = _export("article_md", FIX["article"])
    assert b"# Introduction" in data


def test_article_html():
    data = _export("article_html", FIX["article"])
    text = data.decode("utf-8")
    assert "<html" in text and "<h1>Introduction</h1>" in text
    assert "<strong>bold</strong>" in text


def test_article_docx():
    assert _export("article_docx", FIX["article"]).startswith(PK)


def test_article_latex():
    data = _export("article_latex", FIX["article"])
    text = data.decode("utf-8")
    assert "\\documentclass{article}" in text
    assert "\\section{Introduction}" in text
    assert "\\subsection{Methods}" in text
    assert "\\textbf{bold}" in text and "\\textit{italic}" in text
    assert "\\begin{itemize}" in text and "\\begin{enumerate}" in text
    assert "100\\%" in text and "special\\_chars" in text
    assert "\\author{D. Vlaminck}" in text


# --- reviews --------------------------------------------------------------------------

def test_review_md():
    data = _export("review_md", FIX["review"])
    text = data.decode("utf-8")
    assert "# Review" in text and "## References" in text
    assert "First paper" in text


def test_review_docx():
    assert _export("review_docx", FIX["review"]).startswith(PK)


def test_review_bibtex():
    data = _export("review_bibtex", FIX["review"])
    text = data.decode("utf-8")
    assert text.count("@article{") == 2
    assert "smith2020" in text  # firstauthor+year key
    keys = [line.split("{")[1].rstrip(",") for line in text.splitlines()
            if line.startswith("@article{")]
    assert len(set(keys)) == 2  # deduped
    assert "title = {First paper}" in text
    assert "doi = {10.1/abc}" in text


# --- empty data must still produce valid files ------------------------------------------

def test_empty_objects_export():
    for key, spec in exports.EXPORTERS.items():
        if spec["applies_to"] == "study":
            types = spec.get("study_types") or ("survey", "interview")
            if "survey" in types:
                _export(key, FIX["empty_survey"])
            if "interview" in types:
                _export(key, FIX["empty_interview"])
        elif spec["applies_to"] == "article":
            _export(key, FIX["empty_article"])
        else:
            _export(key, FIX["empty_review"])


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"ERROR {name}: {exc!r}")
    sys.exit(1 if failed else 0)
