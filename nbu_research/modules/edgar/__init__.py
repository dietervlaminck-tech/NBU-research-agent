"""SEC EDGAR connector.

Free SEC data, no key required. Two tools:

1. Financial panel builder — paste tickers, pick XBRL concepts and a year
   range, and a background job assembles a tidy company x year panel into a
   `datasets` row (via datasets.store.from_dataframe) ready for analysis/export.
2. Filing browser + AI analysis — list a company's recent filings and run an
   LLM analysis of a chosen filing (business overview, risk factors, financial
   changes, management tone) as a background job.

This module proves the connector + dataset + background-job path that the
Refinitiv connector will later drop into (see docs/REFINITIV_DESIGN.md).
"""
import markdown
import pandas as pd
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
)

from ... import db
from ...jobs import start_job, get_job, update_progress
from ...llm import complete
from ...config import anthropic_api_key
from ..datasets.store import from_dataframe
from . import client

bp = Blueprint("edgar", __name__)

# Sensible default XBRL concepts offered in the panel builder. label shown in
# the UI; concept is the exact us-gaap tag queried.
COMMON_CONCEPTS = [
    ("Revenues", "Revenues"),
    ("NetIncomeLoss", "Net income / loss"),
    ("Assets", "Total assets"),
    ("Liabilities", "Total liabilities"),
    ("StockholdersEquity", "Stockholders' equity"),
    ("CashAndCashEquivalentsAtCarryingValue", "Cash & equivalents"),
    ("OperatingIncomeLoss", "Operating income / loss"),
]
DEFAULT_CONCEPTS = ["Revenues", "NetIncomeLoss", "Assets"]

FILING_ANALYST_SYSTEM = (
    "You are an expert financial-statement and regulatory-filing analyst with "
    "deep experience reading SEC filings (10-K, 10-Q, 8-K). You write precise, "
    "evidence-grounded analyses for academic researchers. Base every claim on "
    "the filing text provided; never invent figures. Format your answer in "
    "markdown with these sections (use level-2 headings):\n"
    "## Business Overview\n"
    "## Key Risk Factors\n"
    "## Notable Financial Changes\n"
    "## Management Tone & Outlook\n"
    "Be concise but specific, citing concrete items from the filing."
)


def _render_md(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


def _parse_tickers(raw):
    """Split a textarea of tickers (newline- or comma-separated) into a clean,
    de-duplicated, upper-cased list preserving order."""
    parts = []
    for chunk in (raw or "").replace(",", "\n").splitlines():
        t = chunk.strip().upper()
        if t and t not in parts:
            parts.append(t)
    return parts


# --- panel assembly ----------------------------------------------------------

def build_panel(tickers, concepts, year_from, year_to, progress=None):
    """Assemble a long/panel DataFrame of annual XBRL values.

    Columns: ticker, company, fiscal_year, then one column per concept. One row
    per (company, fiscal year) that has at least one requested value. Missing
    concepts become NaN cells. A ticker that fails to resolve or fetch is
    skipped and recorded in `notes` (returned alongside the DataFrame).

    `progress(frac, message)` is an optional callback for job updates.
    """
    rows = {}          # (ticker, fy) -> row dict
    notes = []
    total = max(len(tickers), 1)

    for i, ticker in enumerate(tickers):
        if progress:
            progress(i / total, f"Resolving {ticker} ({i + 1}/{len(tickers)})…")
        try:
            cik10, title = client.ticker_to_cik(ticker)
        except ValueError as e:
            notes.append(f"Skipped {ticker}: {e}")
            continue
        try:
            facts = client.company_facts(cik10)
        except ValueError as e:
            notes.append(f"Skipped {ticker}: {e}")
            continue

        got_any = False
        for concept in concepts:
            series = client.concept_series(cik10, concept, facts=facts)
            for point in series:
                fy = point.get("fy")
                if fy is None:
                    continue
                if year_from and fy < year_from:
                    continue
                if year_to and fy > year_to:
                    continue
                key = (ticker, fy)
                row = rows.get(key)
                if row is None:
                    row = {"ticker": ticker, "company": title, "fiscal_year": fy}
                    rows[key] = row
                row[concept] = point.get("val")
                got_any = True
        if not got_any:
            notes.append(f"No matching annual data for {ticker} in the chosen range")

    columns = ["ticker", "company", "fiscal_year"] + list(concepts)
    ordered = sorted(rows.values(), key=lambda r: (r["ticker"], r["fiscal_year"]))
    df = pd.DataFrame(ordered, columns=columns)
    return df, notes


def _run_panel_job(job_id, project_id, name, description, tickers, concepts,
                   year_from, year_to):
    """Background worker: build the panel and store it as a dataset."""
    def progress(frac, message):
        # Reserve the last 10% for the store step.
        update_progress(job_id, round(frac * 0.9, 3), message)

    df, notes = build_panel(tickers, concepts, year_from, year_to, progress=progress)

    if df.empty:
        msg = "No data assembled. " + ("; ".join(notes) if notes else "")
        raise ValueError(msg.strip() or "No data assembled for the chosen tickers/years.")

    update_progress(job_id, 0.92, "Storing dataset…")
    source_meta = {
        "tickers": tickers,
        "concepts": concepts,
        "years": {"from": year_from, "to": year_to},
        "notes": notes,
    }
    dataset_id = from_dataframe(
        project_id, name, df,
        source="edgar", source_meta=source_meta, description=description,
    )
    update_progress(job_id, 1.0, f"Dataset ready ({len(df)} rows).")
    return {"dataset_id": dataset_id, "n_rows": int(len(df)), "notes": notes}


# --- routes: panel builder ---------------------------------------------------

@bp.route("/")
def index():
    datasets = db.query("datasets", "source = ?", ("edgar",))
    return render_template(
        "edgar/index.html",
        projects=db.query("projects"),
        datasets=datasets,
        common_concepts=COMMON_CONCEPTS,
        default_concepts=DEFAULT_CONCEPTS,
        project_id=request.args.get("project", ""),
    )


@bp.route("/panel", methods=["POST"])
def panel():
    tickers = _parse_tickers(request.form.get("tickers", ""))
    concepts = request.form.getlist("concepts") or DEFAULT_CONCEPTS
    if not tickers:
        return _index_error("Enter at least one ticker.")
    if not concepts:
        return _index_error("Pick at least one financial concept.")

    def _year(field):
        raw = request.form.get(field, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    year_from = _year("year_from")
    year_to = _year("year_to")
    project_id = request.form.get("project_id", "").strip() or None
    name = request.form.get("name", "").strip() or (
        "EDGAR panel: " + ", ".join(tickers[:5]) + ("…" if len(tickers) > 5 else "")
    )
    description = (
        f"SEC EDGAR financial panel for {len(tickers)} company(ies): "
        f"{', '.join(tickers)}. Concepts: {', '.join(concepts)}."
    )

    job_id = start_job(
        "edgar_panel",
        lambda jid: _run_panel_job(
            jid, project_id, name, description, tickers, concepts, year_from, year_to,
        ),
        ref_table="datasets",
    )
    return redirect(url_for(
        "edgar.job_progress", job_id=job_id, kind="panel",
        title="Building financial panel",
    ))


def _index_error(message):
    return render_template(
        "edgar/index.html",
        projects=db.query("projects"),
        datasets=db.query("datasets", "source = ?", ("edgar",)),
        common_concepts=COMMON_CONCEPTS,
        default_concepts=DEFAULT_CONCEPTS,
        project_id=request.args.get("project", ""),
        error=message,
    ), 400


# --- routes: job progress (shared by both flows) -----------------------------

@bp.route("/job/<job_id>")
def job_progress(job_id):
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    return render_template(
        "edgar/job.html",
        job=job,
        kind=request.args.get("kind", "panel"),
        title=request.args.get("title", "Working…"),
    )


@bp.route("/job/<job_id>/done")
def job_done(job_id):
    """Redirect target once a job finishes: jump to its dataset or analysis."""
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    result = job.get("result") or {}
    if job.get("status") == "error":
        return render_template("edgar/job.html", job=job, kind="error",
                               title="Job failed")
    dataset_id = result.get("dataset_id")
    if dataset_id:
        return redirect(url_for("datasets.detail", dataset_id=dataset_id))
    analysis_id = result.get("analysis_id")
    if analysis_id:
        return redirect(url_for("edgar.analysis_detail", analysis_id=analysis_id))
    return render_template("edgar/job.html", job=job, kind="error",
                           title="Job produced no output")


# --- routes: filings browser + analysis --------------------------------------

@bp.route("/filings", methods=["GET", "POST"])
def filings():
    ticker = (request.values.get("ticker", "") or "").strip().upper()
    form_type = request.values.get("form_type", "10-K").strip() or "10-K"
    try:
        count = int(request.values.get("count", "10"))
    except ValueError:
        count = 10
    count = max(1, min(count, 50))

    results = None
    error = None
    company = None
    if ticker:
        try:
            cik10, company = client.ticker_to_cik(ticker)
            forms = None if form_type.upper() == "ALL" else [form_type]
            results = client.recent_filings(cik10, forms=forms, limit=count)
        except ValueError as e:
            error = str(e)

    return render_template(
        "edgar/filings.html",
        ticker=ticker, form_type=form_type, count=count,
        results=results, company=company, error=error,
    )


def _run_analysis_job(job_id, ticker, company, form_type, date, url, model):
    """Background worker: fetch a filing and run the LLM analysis."""
    update_progress(job_id, 0.2, "Fetching filing text…")
    text = client.filing_text(url)
    if not text.strip():
        raise ValueError("The filing document had no extractable text.")

    update_progress(job_id, 0.5, "Analyzing filing with Claude…")
    prompt = (
        f"Analyze the following SEC {form_type} filing for {company} "
        f"({ticker}), filed {date}.\n\nFILING TEXT:\n\n{text}"
    )
    analysis_md = complete(FILING_ANALYST_SYSTEM, prompt, max_tokens=8000)

    update_progress(job_id, 0.95, "Saving analysis…")
    analysis_id = db.insert("articles", {
        "project_id": None,
        "title": f"{form_type} analysis — {company} ({ticker}) {date}",
        "article_type": "edgar_filing_analysis",
        "status": "draft",
        "content_md": analysis_md,
        "metadata": {
            "ticker": ticker, "company": company, "form_type": form_type,
            "filing_date": date, "filing_url": url, "source": "edgar",
        },
    })
    update_progress(job_id, 1.0, "Analysis ready.")
    return {"analysis_id": analysis_id}


@bp.route("/analyze", methods=["POST"])
def analyze():
    if not anthropic_api_key():
        return render_template(
            "edgar/filings.html",
            ticker=request.form.get("ticker", ""), form_type="10-K", count=10,
            results=None, company=None,
            error="Filing analysis needs an ANTHROPIC_API_KEY, which is not "
                  "configured. The panel builder still works without it.",
        ), 400

    ticker = request.form.get("ticker", "").strip().upper()
    company = request.form.get("company", "").strip() or ticker
    form_type = request.form.get("form_type", "").strip() or "filing"
    date = request.form.get("date", "").strip()
    url = request.form.get("url", "").strip()
    if not url:
        return "Missing filing URL", 400

    job_id = start_job(
        "edgar_filing_analysis",
        lambda jid: _run_analysis_job(
            jid, ticker, company, form_type, date, url, None,
        ),
        ref_table="articles",
    )
    return redirect(url_for(
        "edgar.job_progress", job_id=job_id, kind="analysis",
        title=f"Analyzing {form_type} — {company}",
    ))


@bp.route("/analysis/<analysis_id>")
def analysis_detail(analysis_id):
    article = db.get("articles", analysis_id)
    if not article:
        return "Analysis not found", 404
    return render_template(
        "edgar/analysis.html",
        article=article,
        analysis_html=_render_md(article.get("content_md", "")),
        meta=article.get("metadata") or {},
    )
