"""PDF ingestion: every uploaded PDF becomes a source with extracted fulltext.

Extraction is pypdf-only (no OCR): encrypted or unreadable files are rejected
with a clear per-file message, scanned image PDFs yield "no extractable text".
Text is whitespace-normalized and capped so a 600-page handbook cannot blow up
the database row or downstream LLM prompts.
"""
import io
import os
import re

from pypdf import PdfReader

from ... import db

MAX_FULLTEXT_CHARS = 150_000
ABSTRACT_CHARS = 800

_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n\s*\n\s*\n+")


def _open_reader(file_bytes):
    """Parse PDF bytes; raise ValueError with a user-facing message on failure."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            raise ValueError(
                "PDF is encrypted/password-protected — upload a decrypted copy."
            )
        len(reader.pages)  # force lazy parse so corrupt files fail here
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Unreadable PDF ({type(e).__name__}: {e})")
    return reader


def _text_from_reader(reader):
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")  # one broken page must not sink the document
    text = "\n\n".join(parts)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _NL_RE.sub("\n\n", text).strip()
    return text[:MAX_FULLTEXT_CHARS]


def extract_pdf_text(file_bytes):
    """Extract normalized plain text from PDF bytes (capped at 150k chars).

    Raises ValueError for encrypted or unparseable files.
    """
    return _text_from_reader(_open_reader(file_bytes))


def ingest_pdf(review, filename, file_bytes):
    """Turn one uploaded PDF into a source row of `review`.

    Returns a per-file result dict:
    {"filename", "ok", "message", "source_id" (when ok)}.
    """
    filename = os.path.basename(filename or "") or "upload.pdf"
    try:
        reader = _open_reader(file_bytes)
        text = _text_from_reader(reader)
    except ValueError as e:
        return {"filename": filename, "ok": False, "message": str(e)}
    if not text:
        return {
            "filename": filename, "ok": False,
            "message": "No extractable text (scanned image PDF? OCR is not supported).",
        }

    meta_title = ""
    try:
        meta_title = ((reader.metadata.title if reader.metadata else "") or "").strip()
    except Exception:
        pass
    if meta_title.lower() in ("untitled", "unknown"):  # placeholder, not a title
        meta_title = ""
    title = meta_title or os.path.splitext(filename)[0] or "(untitled PDF)"

    source_id = db.insert("sources", {
        "review_id": review["id"],
        "project_id": review.get("project_id"),
        "title": title,
        "abstract": text[:ABSTRACT_CHARS],
        "fulltext": text,
        "meta": {"pdf": {
            "filename": filename,
            "pages": len(reader.pages),
            "chars": len(text),
        }},
    })
    return {
        "filename": filename, "ok": True, "source_id": source_id,
        "message": f"Added as source “{title}” ({len(reader.pages)} pages, {len(text):,} chars extracted).",
    }


def ingest_uploaded_pdfs(review, files):
    """Process a multi-file upload; returns one result dict per file."""
    results = []
    for f in files or []:
        if not f or not (f.filename or "").strip():
            continue
        results.append(ingest_pdf(review, f.filename, f.read()))
    if not results:
        results.append({"filename": "", "ok": False, "message": "No PDF files selected."})
    return results
