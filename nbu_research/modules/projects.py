from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort

from .. import db, llm
from ..auth import (
    current_user, project_role, require_project_role, check_project_role,
)
from ..prompts import methods_advisor

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
    user = current_user()
    if user and user.get("user_id"):
        # The dev-mode synthetic user never went through the login upsert, so
        # make sure a users row exists before the FK-checked membership insert.
        if not db.get("users", user["user_id"]):
            db.insert("users", {
                "id": user["user_id"],
                "display_name": user.get("display_name", ""),
                "email": user.get("email", ""),
            })
        db.insert("project_members", {
            "project_id": project_id, "user_id": user["user_id"],
            "role": "owner", "added_at": db.now(),
        })
    return redirect(url_for("projects.project_detail", project_id=project_id))


def _members_context(project_id):
    members = db.query("project_members", "project_id = ?", (project_id,),
                       order="added_at ASC")
    users = {u["id"]: u for u in db.query("users", order="")}
    for m in members:
        u = users.get(m["user_id"]) or {}
        m["display_name"] = u.get("display_name") or m["user_id"]
        m["email"] = u.get("email", "")
    invites = db.query("project_invites", "project_id = ?", (project_id,),
                       order="invited_at ASC")
    return members, invites


@bp.route("/projects/<project_id>")
@require_project_role("viewer")
def project_detail(project_id):
    project = db.get("projects", project_id)
    if not project:
        return "Project not found", 404
    studies = db.query("studies", "project_id = ?", (project_id,))
    datasets = db.query("datasets", "project_id = ?", (project_id,))
    reviews = db.query("literature_reviews", "project_id = ?", (project_id,))
    articles = db.query("articles", "project_id = ?", (project_id,))
    analyses = db.query("analyses", "project_id = ?", (project_id,))
    members, invites = _members_context(project_id)
    # Mixed-methods prerequisites: a completed thematic/deductive analysis on a
    # study in this project, plus a survey or dataset to integrate with.
    study_ids = {s["id"] for s in studies}
    qual_done = [a for a in db.query("analyses", "status = ?", ("done",))
                 if a["kind"] in ("thematic", "deductive")
                 and a.get("study_id") in study_ids]
    quant_targets = (
        [("study", s["id"], s["title"]) for s in studies
         if s["study_type"] == "survey"] +
        [("dataset", d["id"], d["name"]) for d in datasets]
    )
    return render_template(
        "projects/detail.html",
        project=project, studies=studies, datasets=datasets, reviews=reviews,
        articles=articles, analyses=analyses,
        members=members, invites=invites,
        my_role=project_role(project_id),
        qual_done=qual_done, quant_targets=quant_targets,
    )


@bp.route("/projects/<project_id>/members", methods=["POST"])
@require_project_role("owner")
def add_member(project_id):
    """Invite by email: existing users become members at once; unknown emails
    are stored as pending invites and converted on their first login."""
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "viewer")
    if role not in ("viewer", "collaborator", "owner") or not email:
        return redirect(url_for("projects.project_detail", project_id=project_id))
    matched = db.query("users", "email = ?", (email,), order="")
    if matched:
        uid = matched[0]["id"]
        existing = db.query("project_members", "project_id = ? AND user_id = ?",
                            (project_id, uid), order="")
        if existing:
            db.update("project_members", existing[0]["id"], {"role": role})
        else:
            db.insert("project_members", {
                "project_id": project_id, "user_id": uid,
                "role": role, "added_at": db.now(),
            })
    else:
        db.insert("project_invites", {
            "project_id": project_id, "email": email,
            "role": role, "invited_at": db.now(),
        })
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/projects/<project_id>/members/<member_id>", methods=["POST"])
@require_project_role("owner")
def update_member(project_id, member_id):
    member = db.get("project_members", member_id)
    if not member or member["project_id"] != project_id:
        abort(404)
    action = request.form.get("action", "")
    if action == "remove":
        db.delete("project_members", member_id)
    elif action == "role":
        role = request.form.get("role", "")
        if role in ("viewer", "collaborator", "owner"):
            db.update("project_members", member_id, {"role": role})
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/projects/<project_id>/invites/<invite_id>/delete", methods=["POST"])
@require_project_role("owner")
def delete_invite(project_id, invite_id):
    inv = db.get("project_invites", invite_id)
    if inv and inv["project_id"] == project_id:
        db.delete("project_invites", invite_id)
    return redirect(url_for("projects.project_detail", project_id=project_id))


@bp.route("/projects/<project_id>/methods-advisor", methods=["POST"])
@require_project_role("collaborator")
def methods_advisor_check(project_id):
    """Advisory methodological review of a planned study design. Never blocks
    — the result is stored on the project for later reference and returned to
    the New Study panel on the project page."""
    project = db.get("projects", project_id)
    if not project:
        abort(404)
    body = request.get_json(silent=True) or {}
    research_question = (body.get("research_question") or
                         project.get("research_question") or "").strip()
    paradigm = (body.get("paradigm") or "not sure").strip()
    intended_method = (body.get("intended_method") or "not sure").strip()
    planned_analysis = (body.get("planned_analysis") or "").strip()

    try:
        result = llm.complete_json(
            methods_advisor.SYSTEM,
            methods_advisor.build_prompt(research_question, paradigm,
                                         intended_method, planned_analysis),
            methods_advisor.SCHEMA,
            max_tokens=4000,
        )
    except Exception as e:
        return jsonify({"error": f"Methods advisor unavailable: {e}"}), 503

    record = {
        "input": {
            "research_question": research_question, "paradigm": paradigm,
            "intended_method": intended_method,
            "planned_analysis": planned_analysis,
        },
        "result": result,
        "checked_at": db.now(),
        "checked_by": (current_user() or {}).get("user_id"),
    }
    db.update("projects", project_id, {"methods_check_json": record})
    return jsonify(result)


@bp.route("/api/projects/<project_id>", methods=["DELETE"])
@require_project_role("owner")
def api_delete_project(project_id):
    db.delete("projects", project_id)
    return jsonify({"ok": True})
