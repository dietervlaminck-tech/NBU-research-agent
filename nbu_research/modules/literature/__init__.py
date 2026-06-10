import markdown
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ... import db
from ...jobs import start_job, get_job
from . import pipeline

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
        lambda jid: pipeline.run_literature_review(review_id, jid),
        ref_table="literature_reviews",
        ref_id=review_id,
    )
    scope["job_id"] = job_id
    db.update("literature_reviews", review_id, {"scope": scope})
    return redirect(url_for("literature.review_detail", review_id=review_id))


@bp.route("/review/<review_id>")
def review_detail(review_id):
    review = db.get("literature_reviews", review_id)
    if not review:
        return "Literature review not found", 404
    sources = db.query("sources", "review_id = ?", (review_id,), order="year DESC, title ASC")
    project = db.get("projects", review["project_id"]) if review["project_id"] else None
    job = None
    job_id = (review.get("scope") or {}).get("job_id")
    if job_id and review["status"] in ("pending", "running"):
        job = get_job(job_id)
    return render_template(
        "literature/detail.html",
        review=review,
        sources=sources,
        project=project,
        job=job,
        report_html=_render_md(review["report_md"]),
    )


@bp.route("/api/review/<review_id>", methods=["DELETE"])
def api_delete_review(review_id):
    for source in db.query("sources", "review_id = ?", (review_id,)):
        db.delete("sources", source["id"])
    db.delete("literature_reviews", review_id)
    return jsonify({"ok": True})
