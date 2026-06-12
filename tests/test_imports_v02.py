"""Tests for v0.2 imports: interview transcript import, multilingual system
prompt, and Qualtrics CSV detection in the datasets upload path.

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research so everything
lands in a throwaway SQLite database, and pops ANTHROPIC_API_KEY (no test here
calls the LLM).
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="nbu_test_imports_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)

import docx  # noqa: E402 (python-docx, used to build a .docx fixture)
import pandas as pd  # noqa: E402

from nbu_research import create_app, db  # noqa: E402
from nbu_research.modules.datasets import qualtrics  # noqa: E402
from nbu_research.modules.interviews.bot import build_system_prompt  # noqa: E402
from nbu_research.modules.interviews.transcripts import (  # noqa: E402
    extract_text, parse_transcript,
)

db.init_db()

_app = create_app()
_app.config["TESTING"] = True


def _client():
    return _app.test_client()


# --- transcript parser --------------------------------------------------

PREFIXED = """Interviewer: Welcome. Can you tell me about your role?
Respondent: Sure, I am a controller.
respondent: I have been doing this for ten years.
  R: Mostly reporting work.
I: Interesting.
Interviewer: What do you enjoy most?
Respondent: The variety,
and the people I work with.
"""


def test_parse_prefixed_turns_with_merging():
    messages = parse_transcript(PREFIXED)
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant", "user"]
    # Consecutive same-speaker lines merge (Respondent/respondent/R:).
    assert "Sure, I am a controller." in messages[1]["content"]
    assert "ten years" in messages[1]["content"]
    assert "Mostly reporting work." in messages[1]["content"]
    # Consecutive interviewer lines merge too (I: + Interviewer:).
    assert "Interesting." in messages[2]["content"]
    assert "What do you enjoy most?" in messages[2]["content"]
    # Un-prefixed continuation line stays inside the current turn.
    assert "and the people I work with." in messages[3]["content"]
    # Prefixes are stripped from the content.
    assert "Interviewer:" not in messages[0]["content"]
    assert messages[0]["content"].startswith("Welcome.")


def test_parse_unprefixed_fallback_is_single_user_turn():
    text = "Just a plain narrative answer.\nNo speaker labels anywhere."
    messages = parse_transcript(text)
    assert messages == [{"role": "user", "content": text}]


def test_parse_empty_text():
    assert parse_transcript("") == []
    assert parse_transcript("   \n  \n") == []


def test_extract_text_txt_encoding_fallback():
    assert extract_text("a.txt", "Café".encode("utf-8")) == "Café"
    assert extract_text("a.txt", "Café".encode("latin-1")) == "Café"


def _docx_bytes(lines):
    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_extract_text_docx():
    raw = _docx_bytes(["Interviewer: Hello there.", "Respondent: Hi."])
    text = extract_text("interview.docx", raw)
    messages = parse_transcript(text)
    assert [m["role"] for m in messages] == ["assistant", "user"]
    assert messages[0]["content"] == "Hello there."
    assert messages[1]["content"] == "Hi."


# --- import route -------------------------------------------------------

def test_import_route_creates_study_and_completed_sessions():
    client = _client()
    docx_raw = _docx_bytes([
        "Interviewer: How do you plan budgets?",
        "Respondent: Quarterly, mostly.",
    ])
    res = client.post(
        "/interviews/import",
        data={
            "title": "Imported Study",
            "research_question": "How do controllers plan?",
            "transcripts": [
                (io.BytesIO(PREFIXED.encode("utf-8")), "alice_interview.txt"),
                (io.BytesIO(docx_raw), "bob.docx"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert res.status_code == 302

    studies = db.query("studies", "title = ?", ("Imported Study",))
    assert len(studies) == 1
    study = studies[0]
    assert study["study_type"] == "interview"
    assert study["config"]["imported"] is True
    assert study["config"]["interview_outline"] == "(imported transcripts)"
    assert res.headers["Location"].endswith(f"/interviews/dashboard/{study['id']}")

    sessions = db.query("sessions", "study_id = ?", (study["id"],), order="started_at")
    assert len(sessions) == 2
    names = {s["respondent_name"] for s in sessions}
    assert names == {"alice_interview", "bob"}  # filename stems
    for s in sessions:
        assert s["status"] == "completed"
        assert s["completed_at"]
        for m in s["messages"]:
            assert set(m.keys()) == {"role", "content"}
            assert m["role"] in ("assistant", "user")
            assert m["content"].strip()

    bob = next(s for s in sessions if s["respondent_name"] == "bob")
    assert [m["role"] for m in bob["messages"]] == ["assistant", "user"]
    assert bob["messages"][1]["content"] == "Quarterly, mostly."

    # The dashboard renders and hides the live link box for imported studies.
    page = client.get(f"/interviews/dashboard/{study['id']}")
    assert page.status_code == 200
    assert b'id="interviewUrl"' not in page.data  # share-link box hidden
    assert b"Imported study" in page.data


def test_import_route_rejects_missing_files_and_bad_extensions():
    client = _client()
    res = client.post(
        "/interviews/import",
        data={"title": "X", "research_question": "Y"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400

    res = client.post(
        "/interviews/import",
        data={
            "title": "X", "research_question": "Y",
            "transcripts": [(io.BytesIO(b"hi"), "notes.pdf")],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 400


# --- multilingual system prompt ------------------------------------------

def _study(config):
    return {
        "research_question": "How do people choose careers?",
        "config": config,
    }


def test_system_prompt_language_instruction_for_nl():
    prompt = build_system_prompt(_study({"interview_outline": "1. Ask.", "language": "nl"}))
    assert "Interview Language" in prompt
    assert "Dutch" in prompt
    # Termination codes section is untouched.
    assert "x7y8" in prompt and "5j3k" in prompt


def test_system_prompt_no_language_instruction_for_en_or_unset():
    for config in ({"interview_outline": "1. Ask.", "language": "en"},
                   {"interview_outline": "1. Ask."}):
        prompt = build_system_prompt(_study(config))
        assert "Interview Language" not in prompt
        assert "x7y8" in prompt


def test_system_prompt_free_text_language_passthrough():
    prompt = build_system_prompt(_study({"interview_outline": "", "language": "Italian"}))
    assert "Interview Language" in prompt
    assert "Italian" in prompt


# --- Qualtrics CSV detection ----------------------------------------------

QUALTRICS_CSV = (
    'StartDate,EndDate,Status,IPAddress,Progress,Duration (in seconds),Finished,'
    'RecordedDate,ResponseId,RecipientLastName,RecipientFirstName,RecipientEmail,'
    'ExternalReference,LocationLatitude,LocationLongitude,DistributionChannel,'
    'UserLanguage,Q1,Q2,Q3\n'
    '"Start Date","End Date","Response Type","IP Address","Progress",'
    '"Duration (in seconds)","Finished","Recorded Date","Response ID",'
    '"Recipient Last Name","Recipient First Name","Recipient Email",'
    '"External Data Reference","Location Latitude","Location Longitude",'
    '"Distribution Channel","User Language",'
    '"How satisfied are you with your job?","How old are you?",'
    '"Which department do you work in?"\n'
    '"{""ImportId"":""startDate"",""timeZone"":""Z""}","{""ImportId"":""endDate""}",'
    '"{""ImportId"":""status""}","{""ImportId"":""ipAddress""}",'
    '"{""ImportId"":""progress""}","{""ImportId"":""duration""}",'
    '"{""ImportId"":""finished""}","{""ImportId"":""recordedDate""}",'
    '"{""ImportId"":""_recordId""}","{""ImportId"":""recipientLastName""}",'
    '"{""ImportId"":""recipientFirstName""}","{""ImportId"":""recipientEmail""}",'
    '"{""ImportId"":""externalDataReference""}","{""ImportId"":""locationLatitude""}",'
    '"{""ImportId"":""locationLongitude""}","{""ImportId"":""distributionChannel""}",'
    '"{""ImportId"":""userLanguage""}","{""ImportId"":""QID1""}",'
    '"{""ImportId"":""QID2""}","{""ImportId"":""QID3""}"\n'
    '2026-01-01,2026-01-01,0,1.2.3.4,100,120,1,2026-01-01,R_1,Doe,Jane,j@x.com,'
    ',52.1,4.3,anonymous,EN,4,34,Sales\n'
    '2026-01-02,2026-01-02,0,1.2.3.5,100,95,1,2026-01-02,R_2,Roe,John,r@x.com,'
    ',51.9,4.4,anonymous,EN,5,41,Engineering\n'
)


def test_qualtrics_detect():
    assert qualtrics.detect(QUALTRICS_CSV) is True
    assert qualtrics.detect("a,b\n1,2\n3,4\n") is False
    assert qualtrics.detect("a,b\n1,2\n") is False  # fewer than 3 rows


def test_qualtrics_parse_drops_metadata_and_maps_labels():
    df, labels = qualtrics.parse(QUALTRICS_CSV)
    assert list(df.columns) == ["Q1", "Q2", "Q3"]
    assert len(df) == 2
    assert labels["Q1"] == "How satisfied are you with your job?"
    assert labels["Q3"] == "Which department do you work in?"
    assert df["Q2"].tolist() == [34, 41]


def test_qualtrics_upload_route():
    client = _client()
    res = client.post(
        "/datasets/upload",
        data={
            "name": "Qualtrics Import",
            "file": (io.BytesIO(QUALTRICS_CSV.encode("utf-8")), "export.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert res.status_code == 302

    dataset = db.query("datasets", "name = ?", ("Qualtrics Import",))[0]
    assert dataset["source_meta"]["qualtrics"] is True
    ids = [c["id"] for c in dataset["columns"]]
    labels = {c["id"]: c["label"] for c in dataset["columns"]}
    kinds = {c["id"]: c["kind"] for c in dataset["columns"]}
    assert ids == ["q1", "q2", "q3"]  # row-1 ids, slugged analysis-safe
    assert labels["q1"] == "How satisfied are you with your job?"
    assert kinds["q2"] == "numeric"
    assert kinds["q3"] == "categorical"
    assert dataset["n_rows"] == 2
    # No Qualtrics metadata columns survive.
    assert not any("startdate" in i or "responseid" in i for i in ids)

    # Data rows are intact (headers 2-3 skipped).
    from nbu_research.modules.datasets import store
    df = store.dataframe(dataset)
    assert df["q2"].tolist() == [34, 41]
    assert df["q3"].tolist() == ["Sales", "Engineering"]


def test_plain_csv_upload_unchanged():
    client = _client()
    plain = "Age,Department\n21,Sales\n34,Eng\n45,Sales\n"
    res = client.post(
        "/datasets/upload",
        data={
            "name": "Plain CSV",
            "file": (io.BytesIO(plain.encode("utf-8")), "plain.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert res.status_code == 302
    dataset = db.query("datasets", "name = ?", ("Plain CSV",))[0]
    assert "qualtrics" not in (dataset["source_meta"] or {})
    labels = {c["id"]: c["label"] for c in dataset["columns"]}
    assert labels == {"age": "Age", "department": "Department"}
    assert dataset["n_rows"] == 3


# --- render checks ---------------------------------------------------------

def test_interviews_index_has_import_form_and_language_select():
    page = _client().get("/interviews/")
    assert page.status_code == 200
    assert b'action="/interviews/import"' in page.data
    assert b'name="transcripts"' in page.data
    assert b'name="language"' in page.data


def test_run_page_has_mic_button():
    study_id = db.insert("studies", {
        "study_type": "interview",
        "title": "Voice study",
        "research_question": "RQ",
        "config": {"interview_outline": "1. Ask.", "language": "nl"},
    })
    page = _client().get(f"/interviews/run/{study_id}")
    assert page.status_code == 200
    assert b'id="micBtn"' in page.data
    assert b'data-speech-lang="nl-NL"' in page.data


def test_datasets_index_renders():
    page = _client().get("/datasets/")
    assert page.status_code == 200
    assert b"Qualtrics" in page.data
