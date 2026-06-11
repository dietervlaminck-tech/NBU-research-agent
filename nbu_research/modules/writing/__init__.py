import markdown
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ... import db
from ...jobs import start_job, get_job
from . import pipeline

bp = Blueprint("writing", __name__)

ARTICLE_TYPES = [
    ("empirical", "Empirical (IMRaD)"),
    ("literature_review", "Literature review"),
    ("conceptual", "Conceptual / theoretical"),
    ("methods", "Methods paper"),
]


def _render_md(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


def _project_assets():
    """Completed literature reviews and studies that have completed analyses."""
    reviews = db.query("literature_reviews", "status = ?", ("done",))
    analyses = db.query("analyses", "status = ?", ("done",))
    study_ids = {a["study_id"] for a in analyses if a.get("study_id")}
    studies = [s for s in db.query("studies") if s["id"] in study_ids]
    return reviews, studies


@bp.route("/")
def index():
    articles = db.query("articles")
    projects = db.query("projects")
    reviews, studies = _project_assets()
    return render_template(
        "writing/index.html",
        articles=articles,
        projects=projects,
        reviews=reviews,
        studies=studies,
        article_types=ARTICLE_TYPES,
        project_id=request.args.get("project", ""),
    )


@bp.route("/create", methods=["POST"])
def create():
    title = request.form.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    article_type = request.form.get("article_type", "empirical")
    if article_type not in dict(ARTICLE_TYPES):
        article_type = "empirical"
    project_id = request.form.get("project_id", "").strip() or None

    metadata = {
        "style_note": request.form.get("style_note", "").strip(),
        "review_ids": request.form.getlist("review_ids"),
        "study_ids": request.form.getlist("study_ids"),
    }
    article_id = db.insert("articles", {
        "project_id": project_id,
        "title": title,
        "article_type": article_type,
        "status": "generating",
        "metadata": metadata,
    })
    job_id = start_job(
        "article_generation",
        lambda jid: pipeline.run_article_generation(article_id, jid),
        ref_table="articles",
        ref_id=article_id,
    )
    metadata["job_id"] = job_id
    db.update("articles", article_id, {"metadata": metadata})
    return redirect(url_for("writing.article_detail", article_id=article_id))


@bp.route("/article/<article_id>")
def article_detail(article_id):
    article = db.get("articles", article_id)
    if not article:
        return "Article not found", 404
    metadata = article.get("metadata") or {}
    project = db.get("projects", article["project_id"]) if article["project_id"] else None
    job = None
    if article["status"] in ("generating", "revising") and metadata.get("job_id"):
        job = get_job(metadata["job_id"])
    return render_template(
        "writing/detail.html",
        article=article,
        project=project,
        job=job,
        content_html=_render_md(article["content_md"]),
        outline_html=_render_md(article["outline_md"]),
        review_html=_render_md(metadata.get("review_md", "")),
    )


@bp.route("/article/<article_id>/revise", methods=["POST"])
def revise_article(article_id):
    article = db.get("articles", article_id)
    if not article:
        return "Article not found", 404
    instructions = request.form.get("instructions", "").strip()
    if not instructions:
        return jsonify({"error": "Revision instructions are required"}), 400

    metadata = article.get("metadata") or {}
    job_id = start_job(
        "article_revision",
        lambda jid: pipeline.run_article_revision(article_id, instructions, jid),
        ref_table="articles",
        ref_id=article_id,
    )
    metadata["job_id"] = job_id
    db.update("articles", article_id, {"status": "revising", "metadata": metadata})
    return redirect(url_for("writing.article_detail", article_id=article_id))


@bp.route("/article/<article_id>/disclosure")
def article_disclosure(article_id):
    """AI disclosure statement, generated from the article's recorded AI
    history (jobs + ai_usage_log). Cached in the article metadata; pass
    ?refresh=1 to regenerate after further AI edits."""
    from . import disclosure as disclosure_mod
    article = db.get("articles", article_id)
    if not article:
        return "Article not found", 404
    metadata = article.get("metadata") or {}
    cached = metadata.get("disclosure")
    if cached and request.args.get("refresh") != "1":
        result, error = cached, None
    else:
        try:
            result = disclosure_mod.generate_disclosure(article)
            metadata["disclosure"] = result
            db.update("articles", article_id, {"metadata": metadata})
            error = None
        except Exception as e:
            result, error = None, str(e)
    return render_template("writing/disclosure.html",
                           article=article, disclosure=result, error=error)


@bp.route("/api/article/<article_id>", methods=["DELETE"])
def api_delete_article(article_id):
    db.delete("articles", article_id)
    return jsonify({"ok": True})
