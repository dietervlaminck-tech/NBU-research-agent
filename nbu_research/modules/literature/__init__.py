import markdown
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ... import db
from ...jobs import start_job, get_job
from . import enrich, pdfs, pipeline

bp = Blueprint("literature", __name__)

DEPTH_CHOICES = [
    ("quick", "Quick — ~5 searches per angle"),
    ("standard", "Standard — ~10 searches per angle"),
    ("deep", "Deep — ~15 searches per angle"),
]


def _render_md(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


@bp.route("/")
def index():
    reviews = db.query("literature_reviews")
    projects = db.query("projects")
    return render_template(
        "literature/index.html",
        reviews=reviews,
        projects=projects,
        depth_choices=DEPTH_CHOICES,
        project_id=request.args.get("project", ""),
    )


@bp.route("/create", methods=["POST"])
def create():
    research_question = request.form.get("research_question", "").strip()
    if not research_question:
        return jsonify({"error": "Research question is required"}), 400

    depth = request.form.get("depth", "standard")
    if depth not in pipeline.DEPTH_SEARCHES:
        depth = "standard"
    scope = {
        "discipline": request.form.get("discipline", "").strip(),
        "year_from": request.form.get("year_from", "").strip(),
        "year_to": request.form.get("year_to", "").strip(),
        "source_types": request.form.get("source_types", "").strip(),
        "depth": depth,
    }
    project_id = request.form.get("project_id", "").strip() or None

    review_id = db.insert("literature_reviews", {
        "project_id": project_id,
        "research_question": research_question,
        "scope": scope,
        "status": "pending",
    })
    job_id = start_job(
        "literature_review",
        {"review_id": review_id},
        ref_table="literature_reviews",
        ref_id=review_id,
    )
    scope["job_id"] = job_id
    db.update("literature_reviews", review_id, {"scope": scope})
    return redirect(url_for("literature.review_detail", review_id=review_id))


@bp.route("/review/<review_id>")
def review_detail(review_id):
    return _render_detail(review_id)


def _render_detail(review_id, pdf_results=None):
    review = db.get("literature_reviews", review_id)
    if not review:
        return "Literature review not found", 404
    sources = db.query("sources", "review_id = ?", (review_id,), order="year DESC, title ASC")
    project = db.get("projects", review["project_id"]) if review["project_id"] else None
    job = None
    job_id = (review.get("scope") or {}).get("job_id")
    if job_id and review["status"] in ("pending", "running"):
        job = get_job(job_id)
    # Auxiliary background job (enrichment / re-synthesis) passed via ?job=…
    aux_job = None
    aux_job_id = request.args.get("job", "")
    if aux_job_id:
        aux_job = get_job(aux_job_id)
    enriched_stamps = [
        (s.get("meta") or {}).get("enriched_at") for s in sources
        if isinstance(s.get("meta"), dict)
    ]
    last_enriched = max([t for t in enriched_stamps if t], default="")
    has_fulltext = any((s.get("fulltext") or "").strip() for s in sources)
    return render_template(
        "literature/detail.html",
        review=review,
        sources=sources,
        project=project,
        job=job,
        aux_job=aux_job,
        last_enriched=last_enriched,
        has_fulltext=has_fulltext,
        pdf_results=pdf_results,
        report_html=_render_md(review["report_md"]),
    )


@bp.route("/review/<review_id>/enrich", methods=["POST"])
def enrich_review(review_id):
    if not db.get("literature_reviews", review_id):
        return "Literature review not found", 404
    job_id = enrich.start_enrich_job(review_id)
    return redirect(url_for("literature.review_detail", review_id=review_id, job=job_id))


@bp.route("/review/<review_id>/pdfs", methods=["POST"])
def upload_pdfs(review_id):
    review = db.get("literature_reviews", review_id)
    if not review:
        return "Literature review not found", 404
    results = pdfs.ingest_uploaded_pdfs(review, request.files.getlist("pdfs"))
    return _render_detail(review_id, pdf_results=results)


@bp.route("/review/<review_id>/resynthesize", methods=["POST"])
def resynthesize(review_id):
    if not db.get("literature_reviews", review_id):
        return "Literature review not found", 404
    job_id = start_job(
        "resynthesize_review", {"review_id": review_id},
        ref_table="literature_reviews", ref_id=review_id,
    )
    return redirect(url_for("literature.review_detail", review_id=review_id, job=job_id))


@bp.route("/api/review/<review_id>", methods=["DELETE"])
def api_delete_review(review_id):
    for source in db.query("sources", "review_id = ?", (review_id,)):
        db.delete("sources", source["id"])
    db.delete("literature_reviews", review_id)
    return jsonify({"ok": True})
