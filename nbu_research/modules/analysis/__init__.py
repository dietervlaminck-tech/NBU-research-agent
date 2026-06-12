"""Analysis module: quantitative statistics for surveys, thematic analysis
for interviews. Registered under /analysis (see nbu_research/__init__.py)."""
import markdown as md
from flask import Blueprint, render_template, request, redirect, url_for

from ... import db
from ...auth import check_project_role
from . import mixed, qualitative, quantitative

bp = Blueprint("analysis", __name__)

# v0.2: qualitative synchronous kinds (code co-occurrence) dispatch through the
# same registry as the quantitative kinds — one run_analysis path.
quantitative.ANALYSIS_KINDS.update(qualitative.SYNC_KINDS)


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
        ctx["qual_done"] = [a for a in ctx["analyses"]
                            if a["kind"] in ("thematic", "deductive")
                            and a["status"] == "done"]
    if study["study_type"] == "survey":
        ctx["param_specs"] = quantitative.PARAM_SPECS
    return ctx


@bp.route("/study/<study_id>")
def study_hub(study_id):
    study = db.get("studies", study_id)
    if not study:
        return "Study not found", 404
    check_project_role(study.get("project_id"), "viewer")
    return render_template("analysis/study.html", **_hub_context(study))


def _dataset_hub_context(dataset, error=None):
    cols = quantitative.dataframe_columns(dataset)
    return {
        "dataset": dataset,
        "analyses": db.query("analyses", "dataset_id = ?", (dataset["id"],)),
        "numeric_cols": [c for c in cols if c["kind"] == "numeric"],
        "categorical_cols": [c for c in cols if c["kind"] == "categorical"],
        "param_specs": quantitative.PARAM_SPECS,
        "error": error,
    }


@bp.route("/dataset/<dataset_id>")
def dataset_hub(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    check_project_role(dataset.get("project_id"), "viewer")
    return render_template("analysis/dataset.html", **_dataset_hub_context(dataset))


@bp.route("/dataset/<dataset_id>/run", methods=["POST"])
def run_dataset(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    check_project_role(dataset.get("project_id"), "collaborator")
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
    if kind == "cooccurrence":
        return {"codebook_id": form.get("codebook_id")}
    # v0.2 quantitative kinds describe their own form mapping (PARAM_SPECS).
    spec = quantitative.PARAM_SPECS.get(kind)
    if spec:
        mapping = spec["params_from_form"]
        params = {}
        for f in mapping.get("multiselect_fields", []):
            params[f] = form.getlist(f)
        for f in mapping.get("single_fields", []):
            value = (form.get(f) or "").strip()
            if value:
                params[f] = value
        for f in mapping.get("int_fields", []):
            if f in params:
                try:
                    params[f] = int(params[f])
                except (TypeError, ValueError):
                    pass  # the analysis fn validates and raises a clear error
        return params
    return {}


@bp.route("/study/<study_id>/run", methods=["POST"])
def run(study_id):
    study = db.get("studies", study_id)
    if not study:
        return "Study not found", 404
    check_project_role(study.get("project_id"), "collaborator")
    kind = request.form.get("kind", "")

    if kind == "thematic":
        job_id = qualitative.start_thematic_job(study_id)
        return redirect(url_for("analysis.job_progress", job_id=job_id,
                                study_id=study_id))
    if kind == "deductive":
        job_id = qualitative.start_deductive_job(
            study_id, request.form.get("codebook_id", ""))
        return redirect(url_for("analysis.job_progress", job_id=job_id,
                                study_id=study_id))
    if kind == "intercoder":
        job_id = qualitative.start_intercoder_job(
            study_id, request.form.get("analysis_id", ""))
        return redirect(url_for("analysis.job_progress", job_id=job_id,
                                study_id=study_id))

    try:
        analysis_id = quantitative.run_analysis(
            study, kind, _params_from_form(kind, request.form))
    except ValueError as e:
        ctx = _hub_context(study, error=str(e))
        return render_template("analysis/study.html", **ctx), 400
    return redirect(url_for("analysis.result", analysis_id=analysis_id))


@bp.route("/study/<study_id>/codebooks/upload", methods=["POST"])
def upload_codebook(study_id):
    """Import a codebook (JSON or CSV) for deductive coding."""
    study = db.get("studies", study_id)
    if not study:
        return "Study not found", 404
    check_project_role(study.get("project_id"), "collaborator")
    text = ""
    upload = request.files.get("file")
    if upload and upload.filename:
        text = upload.read().decode("utf-8", errors="replace")
    else:
        text = request.form.get("codebook_text", "")
    try:
        codes = qualitative.parse_codebook_upload(text)
    except ValueError as e:
        ctx = _hub_context(study, error=f"Codebook import failed: {e}")
        return render_template("analysis/study.html", **ctx), 400
    db.insert("codebooks", {
        "study_id": study_id,
        "name": request.form.get("name", "").strip() or "Imported codebook",
        "codes": codes,
    })
    return redirect(url_for("analysis.study_hub", study_id=study_id))


@bp.route("/project/<project_id>/mixed", methods=["POST"])
def run_mixed(project_id):
    """Launch a mixed-methods integration (themes × constructs joint display)."""
    project = db.get("projects", project_id)
    if not project:
        return "Project not found", 404
    check_project_role(project_id, "collaborator")
    target = request.form.get("quant_target", "")
    if ":" not in target:
        return "Pick a survey or dataset to integrate.", 400
    quant_kind, quant_id = target.split(":", 1)
    if quant_kind not in ("study", "dataset"):
        return "Pick a survey or dataset to integrate.", 400
    job_id = mixed.start_mixed_job(
        project_id, request.form.get("thematic_analysis_id", ""),
        quant_kind, quant_id)
    return redirect(url_for("analysis.job_progress", job_id=job_id))


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
    target = study or dataset or {}
    check_project_role(target.get("project_id") or analysis.get("project_id"), "viewer")
    # Back-link target: the survey study hub or the dataset hub.
    if study:
        back_url = url_for("analysis.study_hub", study_id=study["id"])
    elif dataset:
        back_url = url_for("analysis.dataset_hub", dataset_id=dataset["id"])
    else:
        back_url = url_for("analysis.index")
    results = analysis.get("results") or {}
    report_html, tables = None, []
    if results.get("report_md"):  # thematic, deductive, intercoder, mixed
        report_html = md.markdown(results.get("report_md", ""),
                                  extensions=["tables", "fenced_code"])
    tables = _extra_tables(analysis["kind"], results)
    if not tables and not report_html:
        tables = quantitative.result_tables(analysis["kind"], results)
    return render_template("analysis/result.html", analysis=analysis, study=study,
                           dataset=dataset, back_url=back_url,
                           results=results, tables=tables, report_html=report_html)


def _extra_tables(kind, results):
    """Tabular renderings for the v0.2 kinds that aren't plain quant tables."""
    if kind == "intercoder":
        rows = [[k.get("name", k.get("code_id")),
                 "—" if k.get("kappa") is None else k.get("kappa"),
                 k.get("percent_agreement"), k.get("n")]
                for k in results.get("kappa_per_code", [])]
        return [("Intercoder agreement (session × code presence)",
                 ["Code", "Cohen's κ", "% agreement", "n sessions"], rows),
                ("Overall", ["Mean κ", "Sessions"],
                 [[results.get("kappa_mean"), results.get("n_sessions")]])]
    if kind == "cooccurrence":
        codes = results.get("codes", [])
        names = [c.get("name", c.get("id")) for c in codes]
        matrix = results.get("matrix", [])
        rows = [[names[i]] + list(row) for i, row in enumerate(matrix)]
        pairs = [[p.get("code_a"), p.get("code_b"), p.get("count")]
                 for p in results.get("pairs", [])]
        return [("Co-occurrence matrix (sessions where both codes appear)",
                 ["Code"] + names, rows),
                ("Top co-occurring pairs", ["Code A", "Code B", "Sessions"], pairs)]
    if kind == "mixed_methods":
        rows = [[c.get("theme"), c.get("construct"), c.get("relation"),
                 c.get("note")] for c in results.get("cells", [])]
        return [("Joint display (themes × constructs)",
                 ["Theme", "Construct", "Relation", "Note"], rows)]
    if kind == "deductive":
        rows = [[c.get("name", c.get("code_id")), c.get("count")]
                for c in results.get("code_counts", [])]
        return [("Code frequencies (fixed codebook)", ["Code", "Segments"], rows)]
    return []
