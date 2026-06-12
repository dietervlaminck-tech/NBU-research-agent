"""Exports module: download studies, articles and literature reviews in
formats for downstream tools (Excel, SPSS, Qualtrics, Atlas.ti/NVivo/MAXQDA,
Word, LaTeX, BibTeX).

Registry contract (docs/INTERFACES.md): `EXPORTERS` maps
`format_key -> {"label", "applies_to", "fn"}` with
`fn(obj_id) -> (bytes, filename, mimetype)`.
"""
import io

from flask import Blueprint, render_template, send_file, abort

from ... import db
from . import tabular, documents, qdpx, qsf, spss, stats_packages, pdf, prereg

bp = Blueprint("exports", __name__)

TABLES = {"study": "studies", "article": "articles",
          "review": "literature_reviews", "analysis": "analyses",
          "project": "projects"}

# Replication scripts exist for quantitative kinds only (not thematic).
QUANT_KINDS = ("descriptives", "reliability", "ttest", "anova",
               "correlation", "regression", "crosstab")

# Extra keys beyond the contract: "tool" (shown in the UI as the target tool)
# and "study_types" (None = both study types; otherwise a restriction).
EXPORTERS = {
    "study_json": {
        "label": "JSON — full study dump", "applies_to": "study",
        "tool": "Any / archival", "study_types": None, "fn": tabular.study_json,
    },
    "study_csv": {
        "label": "CSV — data matrix", "applies_to": "study",
        "tool": "Excel / R / Python", "study_types": None, "fn": tabular.study_csv,
    },
    "study_xlsx": {
        "label": "Excel workbook (.xlsx)", "applies_to": "study",
        "tool": "Excel", "study_types": None, "fn": tabular.study_xlsx,
    },
    "study_sav": {
        "label": "SPSS dataset (.sav)", "applies_to": "study",
        "tool": "SPSS", "study_types": ("survey",), "fn": spss.study_sav,
    },
    "study_qsf": {
        "label": "Qualtrics survey (.qsf)", "applies_to": "study",
        "tool": "Qualtrics", "study_types": ("survey",), "fn": qsf.study_qsf,
    },
    "study_docx": {
        "label": "Transcripts (.docx)", "applies_to": "study",
        "tool": "Word", "study_types": ("interview",), "fn": documents.study_docx,
    },
    "study_md": {
        "label": "Transcripts (.md)", "applies_to": "study",
        "tool": "Any text editor", "study_types": ("interview",), "fn": documents.study_md,
    },
    "study_qdpx": {
        "label": "REFI-QDA project (.qdpx)", "applies_to": "study",
        "tool": "Atlas.ti / NVivo / MAXQDA", "study_types": ("interview",),
        "fn": qdpx.study_qdpx,
    },
    "study_dta": {
        "label": "Stata dataset (.dta)", "applies_to": "study",
        "tool": "Stata", "study_types": ("survey",), "fn": stats_packages.study_dta,
    },
    "study_rds": {
        "label": "R dataset (.rds)", "applies_to": "study",
        "tool": "R / RStudio", "study_types": ("survey",), "fn": stats_packages.study_rds,
    },
    "study_ipynb": {
        "label": "Jupyter notebook with data (.ipynb)", "applies_to": "study",
        "tool": "Python / Jupyter", "study_types": ("survey",),
        "fn": stats_packages.study_ipynb,
    },
    "analysis_zip": {
        "label": "Replication package (.zip) — data + Stata/R/Python + codebook",
        "applies_to": "analysis", "tool": "OSF / journal data policy",
        "kinds": QUANT_KINDS, "fn": stats_packages.analysis_zip,
    },
    "analysis_do": {
        "label": "Stata script (.do)", "applies_to": "analysis",
        "tool": "Stata", "kinds": QUANT_KINDS, "fn": stats_packages.analysis_do,
    },
    "analysis_r": {
        "label": "R script (.R)", "applies_to": "analysis",
        "tool": "R / RStudio", "kinds": QUANT_KINDS, "fn": stats_packages.analysis_r,
    },
    "analysis_py": {
        "label": "Python script (.py)", "applies_to": "analysis",
        "tool": "Python", "kinds": QUANT_KINDS, "fn": stats_packages.analysis_py,
    },
    "article_docx": {
        "label": "Word document (.docx)", "applies_to": "article",
        "tool": "Word", "fn": documents.article_docx,
    },
    "article_md": {
        "label": "Markdown (.md)", "applies_to": "article",
        "tool": "Any text editor", "fn": documents.article_md,
    },
    "article_html": {
        "label": "HTML (.html)", "applies_to": "article",
        "tool": "Web browser", "fn": documents.article_html,
    },
    "article_latex": {
        "label": "LaTeX (.tex)", "applies_to": "article",
        "tool": "LaTeX / Overleaf", "fn": documents.article_latex,
    },
    "article_pdf": {
        "label": "PDF (.pdf)", "applies_to": "article",
        "tool": "Print / sharing", "fn": pdf.article_pdf,
    },
    "project_prereg": {
        "label": "Preregistration package (.zip)", "applies_to": "project",
        "tool": "OSF / preregistration", "fn": prereg.project_prereg,
    },
    "review_md": {
        "label": "Markdown report (.md)", "applies_to": "review",
        "tool": "Any text editor", "fn": documents.review_md,
    },
    "review_docx": {
        "label": "Word report (.docx)", "applies_to": "review",
        "tool": "Word", "fn": documents.review_docx,
    },
    "review_bibtex": {
        "label": "BibTeX references (.bib)", "applies_to": "review",
        "tool": "Zotero / LaTeX / EndNote", "fn": documents.review_bibtex,
    },
}


def _formats_for(applies_to, obj=None):
    """Applicable (key, spec) pairs, filtered by study_type for studies."""
    items = []
    for key, spec in EXPORTERS.items():
        if spec["applies_to"] != applies_to:
            continue
        if applies_to == "study" and obj is not None:
            allowed = spec.get("study_types")
            if allowed and obj.get("study_type") not in allowed:
                continue
        if applies_to == "analysis" and obj is not None:
            allowed = spec.get("kinds")
            if allowed and obj.get("kind") not in allowed:
                continue
        items.append((key, spec))
    return items


def _obj_title(applies_to, obj):
    if applies_to == "review":
        return obj.get("research_question") or "Literature review"
    return obj.get("title") or obj.get("id", "")


@bp.route("/")
def index():
    studies = db.query("studies")
    articles = db.query("articles")
    reviews = db.query("literature_reviews")
    return render_template("exports/index.html",
                           studies=studies, articles=articles, reviews=reviews)


@bp.route("/<applies_to>/<obj_id>")
def formats(applies_to, obj_id):
    table = TABLES.get(applies_to)
    if not table:
        abort(404)
    obj = db.get(table, obj_id)
    if not obj:
        abort(404)
    return render_template(
        "exports/formats.html",
        applies_to=applies_to, obj=obj,
        obj_title=_obj_title(applies_to, obj),
        formats=_formats_for(applies_to, obj),
    )


@bp.route("/<applies_to>/<obj_id>/<format_key>")
def download(applies_to, obj_id, format_key):
    table = TABLES.get(applies_to)
    spec = EXPORTERS.get(format_key)
    if not table or not spec or spec["applies_to"] != applies_to:
        abort(404)
    obj = db.get(table, obj_id)
    if not obj:
        abort(404)
    if applies_to == "study":
        allowed = spec.get("study_types")
        if allowed and obj.get("study_type") not in allowed:
            abort(404)
    if applies_to == "analysis":
        allowed = spec.get("kinds")
        if allowed and obj.get("kind") not in allowed:
            abort(404)
    data, filename, mimetype = spec["fn"](obj_id)
    return send_file(io.BytesIO(data), mimetype=mimetype,
                     as_attachment=True, download_name=filename)
