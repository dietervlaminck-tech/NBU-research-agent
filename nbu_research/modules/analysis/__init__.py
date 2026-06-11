"""Analysis module: quantitative statistics for surveys, thematic analysis
for interviews. Registered under /analysis (see nbu_research/__init__.py)."""
import markdown as md
from flask import Blueprint, render_template, request, redirect, url_for

from ... import db
from . import qualitative, quantitative

bp = Blueprint("analysis", __name__)


@bp.route("/")
def index():
    studies = db.query("studies")
    projects = {p["id"]: p for p in db.query("projects")}
    counts = {}
    for s in studies:
        if s["study_type"] == "survey":
            n = len([r for r in db.query("survey_responses", "study_id = ?",
                                         (s["id"],), order="started_at ASC")
                     if r.get("answers")])
        else:
            n = len(qualitative._codable_sessions(s["id"]))
        counts[s["id"]] = n
    datasets = db.query("datasets")
    return render_template("analysis/index.html",
                           studies=studies, projects=projects, counts=counts,
                           datasets=datasets)


def _hub_context(study, error=None):
    analyses = db.query("analyses", "study_id = ?", (study["id"],))
    ctx = {"study": study, "analyses": analyses, "error": error}
    if study["study_type"] == "survey":
        cols = quantitative.dataframe_columns(study)
        ctx["numeric_cols"] = [c for c in cols if c["kind"] == "numeric"]
        ctx["categorical_cols"] = [c for c in cols if c["kind"] == "categorical"]
        ctx["n_responses"] = len([
            r for r in db.query("survey_responses", "study_id = ?",
                                (study["id"],), order="started_at ASC")
            if r.get("answers")])
    else:
        ctx["codebooks"] = db.query("codebooks", "study_id = ?", (study["id"],))
        ctx["n_sessions"] = len(qualitative._codable_sessions(study["id"]))
    return ctx


@bp.route("/study/<study_id>")
def study_hub(study_id):
    study = db.get("studies", study_id)
    if not study:
        return "Study not found", 404
    return render_template("analysis/study.html", **_hub_context(study))


def _dataset_hub_context(dataset, error=None):
    cols = quantitative.dataframe_columns(dataset)
    return {
        "dataset": dataset,
        "analyses": db.query("analyses", "dataset_id = ?", (dataset["id"],)),
        "numeric_cols": [c for c in cols if c["kind"] == "numeric"],
        "categorical_cols": [c for c in cols if c["kind"] == "categorical"],
        "error": error,
    }


@bp.route("/dataset/<dataset_id>")
def dataset_hub(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    return render_template("analysis/dataset.html", **_dataset_hub_context(dataset))


@bp.route("/dataset/<dataset_id>/run", methods=["POST"])
def run_dataset(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    kind = request.form.get("kind", "")
    try:
        analysis_id = quantitative.run_analysis(
            dataset, kind, _params_from_form(kind, request.form))
    except ValueError as e:
        ctx = _dataset_hub_context(dataset, error=str(e))
        return render_template("analysis/dataset.html", **ctx), 400
    return redirect(url_for("analysis.result", analysis_id=analysis_id))


def _params_from_form(kind, form):
    if kind == "descriptives":
        return {}
    if kind in ("reliability", "correlation"):
        return {"items": form.getlist("items")}
    if kind == "ttest":
        return {"dv": form.get("dv"), "group_col": form.get("group_col")}
    if kind == "anova":
        return {"dv": form.get("dv"), "factor": form.get("factor")}
    if kind == "regression":
        return {"dv": form.get("dv"), "ivs": form.getlist("ivs")}
    if kind == "crosstab":
        return {"var1": form.get("var1"), "var2": form.get("var2")}
    return {}


@bp.route("/study/<study_id>/run", methods=["POST"])
def run(study_id):
    study = db.get("studies", study_id)
    if not study:
        return "Study not found", 404
    kind = request.form.get("kind", "")

    if kind == "thematic":
        job_id = qualitative.start_thematic_job(study_id)
        return redirect(url_for("analysis.job_progress", job_id=job_id,
                                study_id=study_id))

    try:
        analysis_id = quantitative.run_analysis(
            study, kind, _params_from_form(kind, request.form))
    except ValueError as e:
        ctx = _hub_context(study, error=str(e))
        return render_template("analysis/study.html", **ctx), 400
    return redirect(url_for("analysis.result", analysis_id=analysis_id))


@bp.route("/job/<job_id>")
def job_progress(job_id):
    return render_template("analysis/job.html", job_id=job_id,
                           study_id=request.args.get("study_id", ""))


@bp.route("/result/<analysis_id>")
def result(analysis_id):
    analysis = db.get("analyses", analysis_id)
    if not analysis:
        return "Analysis not found", 404
    study = db.get("studies", analysis["study_id"]) if analysis.get("study_id") else None
    dataset = db.get("datasets", analysis["dataset_id"]) if analysis.get("dataset_id") else None
    # Back-link target: the survey study hub or the dataset hub.
    if study:
        back_url = url_for("analysis.study_hub", study_id=study["id"])
    elif dataset:
        back_url = url_for("analysis.dataset_hub", dataset_id=dataset["id"])
    else:
        back_url = url_for("analysis.index")
    results = analysis.get("results") or {}
    report_html, tables = None, []
    if analysis["kind"] == "thematic":
        report_html = md.markdown(results.get("report_md", ""),
                                  extensions=["tables", "fenced_code"])
    else:
        tables = quantitative.result_tables(analysis["kind"], results)
    return render_template("analysis/result.html", analysis=analysis, study=study,
                           dataset=dataset, back_url=back_url,
                           results=results, tables=tables, report_html=report_html)
