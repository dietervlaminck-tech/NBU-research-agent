"""Scholarly enrichment (Crossref/OpenAlex) + PDF ingestion (v0.3 literature)."""
import io
import os
import tempfile

os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("CELERY_BROKER_URL", None)  # force in-process execution

from nbu_research import db, jobs  # noqa: E402
from nbu_research.modules.literature import enrich, pdfs, pipeline  # noqa: E402

db.init_db()


def _make_review(**overrides):
    values = {
        "research_question": "How do dynamic capabilities shape AI adoption?",
        "scope": {"depth": "quick"},
        "status": "done",
    }
    values.update(overrides)
    return db.insert("literature_reviews", values)


def _run_job(kind, payload):
    """Run a registered job synchronously (deterministic; no threads)."""
    job_id = db.insert("jobs", {
        "kind": kind, "ref_table": "", "ref_id": "",
        "status": "pending", "message": "Queued",
    })
    result = jobs.execute(job_id, kind, payload)
    return result, db.get("jobs", job_id)


# --- title-match acceptance ----------------------------------------------------

def test_titles_match_accepts_normalized_equality():
    assert enrich.titles_match(
        "Dynamic Capabilities & Strategic Management!",
        "dynamic capabilities and strategic management",
    ) is False  # "&" vs "and" differ after stripping non-alphanumerics
    assert enrich.titles_match(
        "Dynamic Capabilities and Strategic Management",
        "  dynamic CAPABILITIES, and strategic-management.",
    )


def test_titles_match_accepts_containment_and_rejects_otherwise():
    assert enrich.titles_match(
        "Dynamic capabilities",
        "Dynamic capabilities and strategic management",
    )
    assert enrich.titles_match(
        "Dynamic capabilities and strategic management: a review",
        "Dynamic capabilities and strategic management",
    )
    assert not enrich.titles_match(
        "Dynamic capabilities",
        "Absorptive capacity: a new perspective on learning",
    )
    assert not enrich.titles_match("", "anything")
    assert not enrich.titles_match("anything", "")


# --- enrichment job --------------------------------------------------------------

def test_enrich_job_fills_blanks_respects_existing_and_writes_meta(monkeypatch):
    review_id = _make_review()
    s_blank = db.insert("sources", {
        "review_id": review_id, "title": "Paper One",
        "year": "", "venue": "", "doi": "",
    })
    s_full = db.insert("sources", {
        "review_id": review_id, "title": "Paper Two",
        "year": "1991", "venue": "Existing Journal", "doi": "10.1/x",
    })
    s_unknown = db.insert("sources", {"review_id": review_id, "title": "Unknown Paper"})

    def fake_crossref(doi="", title=""):
        if doi == "10.1/x":
            return {"doi": "10.1/x", "title": "Paper Two", "venue": "Crossref Journal",
                    "year": "2020", "is_referenced_by_count": 7, "authors": ["Smith, J."]}
        if title == "Paper One":
            return {"doi": "10.5555/one", "title": "Paper One", "venue": "Crossref Journal",
                    "year": "2020", "is_referenced_by_count": 3, "authors": ["Lee, K."]}
        return None

    def fake_openalex(doi="", title=""):
        if doi:  # found via DOI only
            return {"doi": doi, "title": "whatever", "venue": "OA Journal", "year": "2021",
                    "cited_by_count": 42, "oa_pdf_url": "https://example.org/p.pdf"}
        return None

    monkeypatch.setattr(enrich, "crossref_lookup", fake_crossref)
    monkeypatch.setattr(enrich, "openalex_lookup", fake_openalex)

    result, job_row = _run_job("enrich_sources", {"review_id": review_id})
    assert job_row["status"] == "done"
    assert result == {"enriched": 2, "no_match": 1, "review_id": review_id}

    blank = db.get("sources", s_blank)
    # blanks filled (Crossref wins over OpenAlex for bibliographic fields)
    assert blank["doi"] == "10.5555/one"
    assert blank["year"] == "2020"
    assert blank["venue"] == "Crossref Journal"
    # citation counts + OA link always go to meta
    assert blank["meta"]["openalex"]["cited_by_count"] == 42
    assert blank["meta"]["openalex"]["oa_pdf_url"] == "https://example.org/p.pdf"
    assert blank["meta"]["crossref"]["is_referenced_by_count"] == 3
    assert blank["meta"]["enriched_at"]
    assert blank["meta"]["no_match"] is False

    full = db.get("sources", s_full)
    # blanks-only policy: non-empty fields never overwritten
    assert full["year"] == "1991"
    assert full["venue"] == "Existing Journal"
    assert full["doi"] == "10.1/x"
    assert full["meta"]["crossref"]["is_referenced_by_count"] == 7
    assert full["meta"]["openalex"]["cited_by_count"] == 42

    unknown = db.get("sources", s_unknown)
    assert unknown["meta"]["no_match"] is True
    assert unknown["meta"]["enriched_at"]
    assert "crossref" not in unknown["meta"]


def test_enrich_source_survives_api_failure(monkeypatch):
    def boom(doi="", title=""):
        raise ValueError("network down")
    monkeypatch.setattr(enrich, "crossref_lookup", boom)
    monkeypatch.setattr(enrich, "openalex_lookup", boom)
    updates = enrich.enrich_source({"id": "x", "title": "Some Paper", "doi": "", "meta": {}})
    assert updates["meta"]["no_match"] is True


# --- PDF ingestion ----------------------------------------------------------------

def _make_pdf_bytes(text="Hello literature world", title=None):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    if title:
        c.setTitle(title)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_pdf_extraction_roundtrip_and_source_creation():
    data = _make_pdf_bytes("Grounded theory in practice", title="A Grounded Theory Study")
    text = pdfs.extract_pdf_text(data)
    assert "Grounded theory in practice" in text
    assert len(text) <= pdfs.MAX_FULLTEXT_CHARS

    review_id = _make_review()
    review = db.get("literature_reviews", review_id)
    result = pdfs.ingest_pdf(review, "grounded_theory.pdf", data)
    assert result["ok"], result
    src = db.get("sources", result["source_id"])
    assert src["review_id"] == review_id
    assert src["title"] == "A Grounded Theory Study"  # PDF metadata title wins
    assert "Grounded theory in practice" in src["fulltext"]
    assert src["abstract"] == src["fulltext"][:pdfs.ABSTRACT_CHARS]
    assert src["meta"]["pdf"]["filename"] == "grounded_theory.pdf"
    assert src["meta"]["pdf"]["pages"] == 1
    assert src["meta"]["pdf"]["chars"] == len(src["fulltext"])


def test_pdf_title_falls_back_to_filename_stem():
    data = _make_pdf_bytes("Some body text")  # no metadata title
    review = db.get("literature_reviews", _make_review())
    result = pdfs.ingest_pdf(review, "teece_1997_dynamic_capabilities.pdf", data)
    assert result["ok"]
    assert db.get("sources", result["source_id"])["title"] == "teece_1997_dynamic_capabilities"


def test_encrypted_and_unreadable_pdfs_rejected():
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_make_pdf_bytes("secret"))))
    writer.encrypt("hunter2")
    buf = io.BytesIO()
    writer.write(buf)

    review = db.get("literature_reviews", _make_review())
    encrypted = pdfs.ingest_pdf(review, "locked.pdf", buf.getvalue())
    assert not encrypted["ok"]
    assert "encrypt" in encrypted["message"].lower() or "password" in encrypted["message"].lower()

    garbage = pdfs.ingest_pdf(review, "not_a_pdf.pdf", b"definitely not a pdf")
    assert not garbage["ok"]
    assert garbage["message"]

    # no sources were created for rejected files
    titles = [s["title"] for s in db.query("sources", "review_id = ?", (review["id"],))]
    assert "locked" not in titles and "not_a_pdf" not in titles


# --- re-synthesis job --------------------------------------------------------------

def test_resynthesize_overwrites_report_and_grounds_in_fulltext(monkeypatch):
    review_id = _make_review()
    db.update("literature_reviews", review_id, {"report_md": "OLD REPORT", "completed_at": None})
    db.insert("sources", {
        "review_id": review_id, "title": "Paper A", "authors": "Smith, J.",
        "year": "2020", "fulltext": "FULLTEXT-MARKER " * 600,  # ~9600 chars > excerpt cap
    })
    db.insert("sources", {"review_id": review_id, "title": "Paper B", "authors": "Lee, K."})

    captured = {}

    def fake_complete(system, prompt, **kwargs):
        captured["system"] = system
        captured["prompt"] = prompt
        return "NEW REPORT"

    monkeypatch.setattr(pipeline, "complete", fake_complete)
    result, job_row = _run_job("resynthesize_review", {"review_id": review_id})
    assert job_row["status"] == "done", job_row["message"]
    assert result == {"review_id": review_id}

    review = db.get("literature_reviews", review_id)
    assert review["report_md"] == "NEW REPORT"
    assert review["completed_at"]
    assert review["status"] == "done"

    # fulltext excerpt included, labelled, and capped at FULLTEXT_EXCERPT_CHARS
    assert "FULLTEXT-MARKER" in captured["prompt"]
    assert "Full-text excerpt — Smith, J. (2020). Paper A" in captured["prompt"]
    max_markers = pipeline.FULLTEXT_EXCERPT_CHARS // len("FULLTEXT-MARKER ") + 1
    assert captured["prompt"].count("FULLTEXT-MARKER") <= max_markers  # capped at ~8000 chars
    # both sources appear in the source list
    assert "Paper A" in captured["prompt"] and "Paper B" in captured["prompt"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
