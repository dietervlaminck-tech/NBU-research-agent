from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from .. import db

bp = Blueprint("projects", __name__, template_folder="../templates")


@bp.route("/")
def index():
    projects = db.query("projects")
    return render_template("projects/index.html", projects=projects)


@bp.route("/projects/create", methods=["POST"])
def create_project():
    title = request.form.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    project_id = db.insert("projects", {
        "title": title,
        "description": request.form.get("description", "").strip(),
        "research_question": request.form.get("research_question", "").strip(),
    })
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/projects/<project_id>")
def project_detail(project_id):
    project = db.get("projects", project_id)
    if not project:
        return "Project not found", 404
    studies = db.query("studies", "project_id = ?", (project_id,))
    reviews = db.query("literature_reviews", "project_id = ?", (project_id,))
    articles = db.query("articles", "project_id = ?", (project_id,))
    analyses = db.query("analyses", "project_id = ?", (project_id,))
    return render_template(
        "projects/detail.html",
        project=project, studies=studies, reviews=reviews,
        articles=articles, analyses=analyses,
    )


@bp.route("/api/projects/<project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    db.delete("projects", project_id)
    return jsonify({"ok": True})
