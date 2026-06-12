import json
import os
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    Response, stream_with_context,
)

from ... import db
from ...auth import check_project_role
from ...config import DEFAULT_INTERVIEW_MODEL
from .bot import DEFAULT_GENERAL_INSTRUCTIONS, FIRST_USER_MESSAGE, stream_interview_reply
from .transcripts import extract_text, parse_transcript

TRANSCRIPT_EXTENSIONS = {".txt", ".docx"}

bp = Blueprint("interviews", __name__)


def _get_interview_study(study_id):
    study = db.get("studies", study_id)
    if not study or study["study_type"] != "interview":
        return None
    return study


def _run_url(study_id):
    return request.url_root.rstrip("/") + url_for("interviews.run_page", study_id=study_id)


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


@bp.route("/")
def index():
    studies = db.query("studies", "study_type = ?", ("interview",))
    return render_template(
        "interviews/index.html",
        studies=studies,
        default_instructions=DEFAULT_GENERAL_INSTRUCTIONS,
        default_model=DEFAULT_INTERVIEW_MODEL,
        project_id=request.args.get("project", ""),
    )


@bp.route("/create", methods=["POST"])
def create_study():
    title = request.form.get("title", "").strip()
    research_question = request.form.get("research_question", "").strip()
    interview_outline = request.form.get("interview_outline", "").strip()
    general_instructions = request.form.get("general_instructions", "").strip()
    model = request.form.get("model", DEFAULT_INTERVIEW_MODEL)
    project_id = request.form.get("project_id", "").strip() or None
    language = request.form.get("language", "en").strip() or "en"
    if language == "other":
        language = request.form.get("language_other", "").strip() or "en"

    if not title or not research_question or not interview_outline:
        return jsonify({"error": "Title, research question, and interview outline are required"}), 400

    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "interview",
        "title": title,
        "research_question": research_question,
        "config": {
            "interview_outline": interview_outline,
            "general_instructions": general_instructions,
            "language": language,
        },
        "model": model,
    })
    return redirect(url_for("interviews.study_created", study_id=study_id))


@bp.route("/import", methods=["POST"])
def import_transcripts():
    """Create a study from uploaded transcript files (.txt/.docx), one
    completed session per file. Imported studies have no live interview link
    and are marked with config.imported."""
    title = request.form.get("title", "").strip()
    research_question = request.form.get("research_question", "").strip()
    files = [f for f in request.files.getlist("transcripts") if f and f.filename]
    project_id = request.form.get("project_id", "").strip() or None

    if not title or not research_question:
        return jsonify({"error": "Title and research question are required"}), 400
    if not files:
        return jsonify({"error": "Please choose at least one transcript file (.txt or .docx)"}), 400
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in TRANSCRIPT_EXTENSIONS:
            return jsonify({"error": f"Unsupported transcript file type '{ext}'. Allowed: .txt, .docx"}), 400

    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "interview",
        "title": title,
        "research_question": research_question,
        "config": {
            "imported": True,
            "interview_outline": "(imported transcripts)",
        },
    })

    for f in files:
        text = extract_text(f.filename, f.read())
        messages = parse_transcript(text)
        now = db.now()
        db.insert("sessions", {
            "id": db.new_id(16),
            "study_id": study_id,
            "respondent_name": os.path.splitext(os.path.basename(f.filename))[0],
            "messages": messages,
            "status": "completed",
            "started_at": now,
            "completed_at": now,
            "duration_seconds": 0,
        })

    return redirect(url_for("interviews.dashboard", study_id=study_id))


@bp.route("/created/<study_id>")
def study_created(study_id):
    study = _get_interview_study(study_id)
    if not study:
        return "Study not found", 404
    return render_template(
        "interviews/study_created.html",
        study=study, interview_url=_run_url(study_id),
    )


@bp.route("/run/<study_id>")
def run_page(study_id):
    study = _get_interview_study(study_id)
    if not study:
        return "Interview not found", 404
    return render_template("interviews/run.html", study=study)


@bp.route("/dashboard/<study_id>")
def dashboard(study_id):
    study = _get_interview_study(study_id)
    if not study:
        return "Study not found", 404
    check_project_role(study.get("project_id"), "viewer")
    sessions = db.query("sessions", "study_id = ?", (study_id,), order="started_at DESC")
    return render_template(
        "interviews/dashboard.html",
        study=study, sessions=sessions, interview_url=_run_url(study_id),
    )


# --- respondent-facing API ---------------------------------------------------

@bp.route("/api/session/create", methods=["POST"])
def api_create_session():
    data = request.get_json()
    study_id = data.get("study_id")
    respondent_name = data.get("respondent_name", "")
    if not study_id or not _get_interview_study(study_id):
        return jsonify({"error": "Invalid study"}), 400
    session_id = db.insert("sessions", {
        "id": db.new_id(16),
        "study_id": study_id,
        "respondent_name": respondent_name,
        "messages": [],
        "status": "active",
        "started_at": db.now(),
    })
    return jsonify({"session_id": session_id})


@bp.route("/api/session/<session_id>/first-message")
def api_first_message(session_id):
    session = db.get("sessions", session_id)
    if not session:
        return "Session not found", 404
    study = _get_interview_study(session["study_id"])
    if not study:
        return "Study not found", 404

    def generate():
        full_text = ""
        for event in stream_interview_reply(study, [{"role": "user", "content": FIRST_USER_MESSAGE}]):
            if event["type"] == "text":
                full_text += event["content"]
                yield _sse({"type": "text", "content": event["content"]})

        messages = [
            {"role": "user", "content": FIRST_USER_MESSAGE},
            {"role": "assistant", "content": full_text},
        ]
        db.update("sessions", session_id, {"messages": messages})
        yield _sse({"type": "done"})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@bp.route("/api/session/<session_id>/message", methods=["POST"])
def api_send_message(session_id):
    session = db.get("sessions", session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    if session["status"] != "active":
        return jsonify({"error": "Interview already completed"}), 400

    study = _get_interview_study(session["study_id"])
    if not study:
        return jsonify({"error": "Study not found"}), 404

    data = request.get_json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    messages = session["messages"]
    messages.append({"role": "user", "content": user_message})
    db.update("sessions", session_id, {"messages": messages})

    def generate():
        full_response = ""
        final_code = None
        closing = None

        for event in stream_interview_reply(study, messages):
            if event["type"] == "text":
                full_response += event["content"]
                yield _sse({"type": "text", "content": event["content"]})
            elif event["type"] == "done":
                final_code = event.get("code")
                closing = event.get("closing")

        messages.append({"role": "assistant", "content": full_response})
        db.update("sessions", session_id, {"messages": messages})

        if final_code in ("complete", "safety"):
            _complete_session(session)
            yield _sse({"type": "closing", "message": closing})

        yield _sse({"type": "done"})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


def _complete_session(session):
    duration = 0
    try:
        started = datetime.fromisoformat(session["started_at"])
        duration = (datetime.now(timezone.utc) - started).total_seconds()
    except (ValueError, TypeError, KeyError):
        pass
    db.update("sessions", session["id"], {
        "status": "completed",
        "completed_at": db.now(),
        "duration_seconds": duration,
    })


# --- researcher-facing API ---------------------------------------------------

@bp.route("/api/study/<study_id>", methods=["DELETE"])
def api_delete_study(study_id):
    study = db.get("studies", study_id)
    if study:
        check_project_role(study.get("project_id"), "collaborator")
    for session in db.query("sessions", "study_id = ?", (study_id,), order=""):
        db.delete("sessions", session["id"])
    db.delete("studies", study_id)
    return jsonify({"ok": True})
