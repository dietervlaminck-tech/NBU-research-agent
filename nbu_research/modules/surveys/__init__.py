"""Native survey engine: builder, distribution, runner, and response dashboard."""

from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ... import db
from ...auth import check_project_role
from .builder import (
    QUESTION_TYPES,
    design_survey,
    normalize_questions,
    validate_answers,
    summarize,
    format_answer,
)
from .scales import SCALE_LIBRARY

bp = Blueprint("surveys", __name__)


def _get_survey(study_id):
    study = db.get("studies", study_id)
    if not study or study.get("study_type") != "survey":
        return None
    config = study.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    config.setdefault("questions", [])
    config.setdefault("welcome_text", "")
    config.setdefault("thankyou_text", "")
    # v0.2 optional config (absent in legacy surveys → behave exactly as before)
    config.setdefault("randomize_questions", False)
    config.setdefault("scale_citations", {})
    study["config"] = config
    return study


@bp.route("/")
def index():
    studies = db.query("studies", "study_type = ?", ("survey",))
    counts = {}
    for r in db.query("survey_responses", order="started_at DESC"):
        counts[r["study_id"]] = counts.get(r["study_id"], 0) + 1
    return render_template(
        "surveys/index.html",
        studies=studies,
        counts=counts,
        project_id=request.args.get("project", ""),
    )


@bp.route("/create", methods=["POST"])
def create():
    title = request.form.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    research_question = request.form.get("research_question", "").strip()
    brief = request.form.get("design_brief", "").strip()
    config = {"questions": [], "welcome_text": "", "thankyou_text": ""}
    if brief:
        # "Design with AI": synchronous call (<1 min) to the survey methodologist.
        config = design_survey(title, research_question, brief)
    study_id = db.insert("studies", {
        "project_id": request.form.get("project_id", "").strip() or None,
        "study_type": "survey",
        "title": title,
        "research_question": research_question,
        "config": config,
    })
    return redirect(url_for("surveys.builder", study_id=study_id))


@bp.route("/builder/<study_id>")
def builder(study_id):
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    return render_template("surveys/builder.html", study=study,
                           question_types=QUESTION_TYPES, scale_library=SCALE_LIBRARY)


@bp.route("/api/<study_id>/questions", methods=["POST"])
def api_save_questions(study_id):
    study = _get_survey(study_id)
    if not study:
        return jsonify({"error": "Survey not found"}), 404
    data = request.get_json(silent=True) or {}
    questions = normalize_questions(data.get("questions", []))
    config = {
        "questions": questions,
        "welcome_text": str(data.get("welcome_text", "")).strip(),
        "thankyou_text": str(data.get("thankyou_text", "")).strip(),
    }
    if data.get("randomize_questions"):
        config["randomize_questions"] = True
    # Instrument citations are derived server-side from the construct tags so
    # they always match the questions actually saved.
    citations = {q["construct"]: SCALE_LIBRARY[q["construct"]]["citation"]
                 for q in questions if q.get("construct") in SCALE_LIBRARY}
    if citations:
        config["scale_citations"] = citations
    db.update("studies", study_id, {"config": config})
    return jsonify({"ok": True, "config": config})


@bp.route("/created/<study_id>")
def created(study_id):
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    survey_url = url_for("surveys.run", study_id=study_id, _external=True)
    return render_template("surveys/created.html", study=study, survey_url=survey_url)


@bp.route("/run/<study_id>")
def run(study_id):
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    return render_template("surveys/run.html", study=study,
                           questions=study["config"]["questions"])


@bp.route("/api/<study_id>/respond", methods=["POST"])
def api_respond(study_id):
    study = _get_survey(study_id)
    if not study:
        return jsonify({"error": "Survey not found"}), 404
    data = request.get_json(silent=True) or {}
    answers, errors = validate_answers(study["config"]["questions"], data.get("answers"))
    if errors:
        return jsonify({"errors": errors}), 400
    db.insert("survey_responses", {
        "study_id": study_id,
        "respondent_name": str(data.get("respondent_name", "")).strip(),
        "answers": answers,
        "status": "completed",
        "started_at": data.get("started_at") or db.now(),
        "completed_at": db.now(),
    })
    return jsonify({"ok": True, "thankyou_text": study["config"]["thankyou_text"]})


@bp.route("/dashboard/<study_id>")
def dashboard(study_id):
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    check_project_role(study.get("project_id"), "viewer")
    questions = study["config"]["questions"]
    responses = db.query("survey_responses", "study_id = ?", (study_id,),
                         order="started_at DESC")
    completed = [r for r in responses if r.get("status") == "completed"]
    completion_rate = round(100 * len(completed) / len(responses)) if responses else 0
    response_rows = [{
        "response": r,
        "answer_values": [format_answer(q, (r.get("answers") or {}).get(q["id"]))
                          for q in questions],
    } for r in responses]
    return render_template(
        "surveys/dashboard.html",
        study=study,
        questions=questions,
        responses=responses,
        completed_count=len(completed),
        completion_rate=completion_rate,
        summaries=summarize(questions, completed),
        response_rows=response_rows,
    )


@bp.route("/api/study/<study_id>", methods=["DELETE"])
def api_delete_study(study_id):
    study = db.get("studies", study_id)
    if study:
        check_project_role(study.get("project_id"), "collaborator")
    study = _get_survey(study_id)
    if not study:
        return jsonify({"error": "Survey not found"}), 404
    for r in db.query("survey_responses", "study_id = ?", (study_id,), order=""):
        db.delete("survey_responses", r["id"])
    db.delete("studies", study_id)
    return jsonify({"ok": True})
