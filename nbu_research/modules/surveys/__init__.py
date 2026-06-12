"""Native survey engine: builder, distribution, runner, and response dashboard.

v0.3 adds the Qualtrics connector (qualtrics_api.py): push a survey to the
researcher's own Qualtrics account, and pull collected responses back as an
analyzable dataset. Pulls land in `datasets` (NOT survey_responses) by design:
Qualtrics QIDs don't map 1:1 to our question ids, and datasets are already
first-class analysis targets.
"""
import io

import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ... import db
from ...auth import check_project_role
from ...credentials import get_credential
from ...jobs import job as job_task, start_job, get_job, update_progress
from .builder import (
    QUESTION_TYPES,
    design_survey,
    normalize_questions,
    validate_answers,
    summarize,
    format_answer,
)
from .scales import SCALE_LIBRARY
from . import qualtrics_api
# Sanctioned cross-module imports for the Qualtrics pull path (same pattern as
# edgar/refinitiv): pulled responses are parsed by the existing Qualtrics CSV
# detector and stored through the canonical dataset helper so column typing
# stays consistent platform-wide.
from ..datasets import qualtrics as qualtrics_csv
from ..datasets.store import from_dataframe

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
    return render_template("surveys/created.html", study=study, survey_url=survey_url,
                           qualtrics_connected=get_credential("qualtrics") is not None)


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
    qualtrics_datasets = [
        d for d in db.query("datasets", "source = ?", ("qualtrics",))
        if (d.get("source_meta") or {}).get("study_id") == study_id
    ]
    return render_template(
        "surveys/dashboard.html",
        study=study,
        questions=questions,
        responses=responses,
        completed_count=len(completed),
        completion_rate=completion_rate,
        summaries=summarize(questions, completed),
        response_rows=response_rows,
        qualtrics_connected=get_credential("qualtrics") is not None,
        qualtrics_datasets=qualtrics_datasets,
        qualtrics_error=request.args.get("qualtrics_error", ""),
        qualtrics_notice=request.args.get("qualtrics_notice", ""),
    )


# --- Qualtrics connector (v0.3) ----------------------------------------------

def _dashboard_with_error(study_id, message):
    return redirect(url_for("surveys.dashboard", study_id=study_id,
                            qualtrics_error=message))


@bp.route("/<study_id>/qualtrics/push", methods=["POST"])
def qualtrics_push(study_id):
    """Import this survey into the researcher's Qualtrics account.

    Synchronous on purpose — it is a single API call."""
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    check_project_role(study.get("project_id"), "collaborator")
    cred = get_credential("qualtrics")
    if not cred:
        # Never error when not connected: dormant-connector pattern.
        return _dashboard_with_error(study_id, "not_connected")
    try:
        survey_id = qualtrics_api.push_survey(
            study, cred.get("api_token", ""), cred.get("datacenter", ""))
    except ValueError as e:
        return _dashboard_with_error(study_id, str(e))
    config = study["config"]
    config["qualtrics"] = {"qualtrics_survey_id": survey_id,
                           "pushed_at": db.now()}
    db.update("studies", study_id, {"config": config})
    return redirect(url_for("surveys.dashboard", study_id=study_id,
                            qualtrics_notice="pushed"))


@job_task("qualtrics_pull")
def _run_qualtrics_pull(job_id, study_id=None, token=None, datacenter=None,
                        qualtrics_survey_id=None):
    """Background worker: export Qualtrics responses into a dataset.

    The token arrives in the job payload (resolved by the route — jobs have no
    request context). Payloads are JSON and visible in the jobs table at the
    same trust boundary as the credential store; the token is never logged."""
    study = db.get("studies", study_id) or {}
    update_progress(job_id, 0.1, "Exporting responses from Qualtrics…")
    csv_text = qualtrics_api.pull_responses(qualtrics_survey_id, token, datacenter)

    update_progress(job_id, 0.7, "Parsing responses…")
    if qualtrics_csv.detect(csv_text):
        df, labels = qualtrics_csv.parse(csv_text)
    else:  # non-legacy export shape: plain single-header CSV
        df, labels = pd.read_csv(io.StringIO(csv_text)), None
    if df.empty:
        raise ValueError("The Qualtrics export contained no responses yet.")

    update_progress(job_id, 0.9, "Storing dataset…")
    dataset_id = from_dataframe(
        study.get("project_id"),
        f"Qualtrics responses: {study.get('title') or 'survey'}",
        df,
        source="qualtrics",
        source_meta={"study_id": study_id,
                     "qualtrics_survey_id": qualtrics_survey_id,
                     "pulled_at": db.now()},
        description=f"Responses pulled from Qualtrics survey "
                    f"{qualtrics_survey_id} for study “{study.get('title', '')}”.",
        labels=labels,
    )
    update_progress(job_id, 1.0, f"Dataset ready ({len(df)} rows).")
    return {"dataset_id": dataset_id, "n_rows": int(len(df))}


@bp.route("/<study_id>/qualtrics/pull", methods=["POST"])
def qualtrics_pull(study_id):
    """Start a background job that pulls Qualtrics responses into a dataset."""
    study = _get_survey(study_id)
    if not study:
        return "Survey not found", 404
    check_project_role(study.get("project_id"), "collaborator")
    cred = get_credential("qualtrics")
    if not cred:
        return _dashboard_with_error(study_id, "not_connected")
    qualtrics_survey_id = (study["config"].get("qualtrics") or {}).get(
        "qualtrics_survey_id")
    if not qualtrics_survey_id:
        return _dashboard_with_error(
            study_id, "Push the survey to Qualtrics before pulling responses.")
    job_id = start_job("qualtrics_pull", {
        "study_id": study_id,
        "token": cred.get("api_token", ""),
        "datacenter": cred.get("datacenter", ""),
        "qualtrics_survey_id": qualtrics_survey_id,
    }, ref_table="datasets")
    return redirect(url_for("surveys.qualtrics_job",
                            study_id=study_id, job_id=job_id))


@bp.route("/<study_id>/qualtrics/job/<job_id>")
def qualtrics_job(study_id, job_id):
    """Minimal polling page for the pull job (edgar/job.html pattern)."""
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    return render_template("surveys/qualtrics_job.html",
                           study_id=study_id, job=job)


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
