"""Datasets module: upload tabular files (CSV/XLSX/SAV/DTA) into first-class
analysis targets, list and preview them, and hand them off to the analysis hub.
"""
import io
import os

from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify, send_file,
)

from ... import db
from ...auth import check_project_role
from . import qualtrics, store

bp = Blueprint("datasets", __name__, template_folder="../templates")

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".sav", ".dta"}


def _projects():
    return db.query("projects")


def _dataset_view(dataset):
    """Augment a dataset row with project title and column count for the UI."""
    d = dict(dataset)
    project = db.get("projects", dataset.get("project_id")) if dataset.get("project_id") else None
    d["project_title"] = project["title"] if project else None
    d["n_columns"] = len(dataset.get("columns") or [])
    return d


@bp.route("/")
def index():
    datasets = [_dataset_view(d) for d in db.query("datasets")]
    selected_project = request.args.get("project", "")
    return render_template(
        "datasets/index.html",
        datasets=datasets,
        projects=_projects(),
        selected_project=selected_project,
    )


def _read_dataframe(filename, raw):
    """Parse uploaded bytes into (DataFrame, labels-or-None) based on file
    extension. `labels` maps column name -> human-readable label and is only
    set for auto-detected Qualtrics legacy CSV exports. Raises ValueError with
    a human-readable message on failure."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".csv":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        if qualtrics.detect(text):
            return qualtrics.parse(text)
        return pd.read_csv(io.StringIO(text)), None
    if ext in (".xlsx", ".xls"):
        # First sheet only.
        return pd.read_excel(io.BytesIO(raw), sheet_name=0), None
    if ext == ".sav":
        df, _meta = pyreadstat.read_sav(_to_tempfile(raw, ext))
        return df, None
    if ext == ".dta":
        df, _meta = pyreadstat.read_dta(_to_tempfile(raw, ext))
        return df, None
    raise ValueError(f"Unsupported file type: {ext}")


def _to_tempfile(raw, ext):
    """pyreadstat reads from a path; write the upload to a temp file."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return path


@bp.route("/upload", methods=["POST"])
def upload():
    def fail(message):
        return render_template(
            "datasets/index.html",
            datasets=[_dataset_view(d) for d in db.query("datasets")],
            projects=_projects(),
            selected_project=request.form.get("project_id", ""),
            error=message,
        ), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return fail("Please choose a file to upload.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return fail(f"Unsupported file type '{ext}'. Allowed: CSV, XLSX, SAV, DTA.")

    raw = file.read()
    if not raw:
        return fail("The uploaded file is empty.")

    try:
        df, labels = _read_dataframe(file.filename, raw)
    except Exception as e:  # noqa: BLE001 — surface any parse error to the user
        return fail(f"Could not read '{file.filename}': {e}")

    if df is None or df.shape[1] == 0:
        return fail("The file has no columns to import.")

    name = request.form.get("name", "").strip() or os.path.splitext(file.filename)[0]
    project_id = request.form.get("project_id", "").strip() or None
    description = request.form.get("description", "").strip()

    source_meta = {"filename": file.filename, "ext": ext}
    if labels is not None:
        source_meta["qualtrics"] = True

    dataset_id = store.from_dataframe(
        project_id, name, df,
        source="upload",
        source_meta=source_meta,
        description=description,
        labels=labels,
    )
    return redirect(url_for("datasets.detail", dataset_id=dataset_id))


@bp.route("/<dataset_id>")
def detail(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    check_project_role(dataset.get("project_id"), "viewer")
    column_ids, rows = store.preview(dataset, n=20)
    return render_template(
        "datasets/detail.html",
        dataset=_dataset_view(dataset),
        column_ids=column_ids,
        rows=rows,
    )


@bp.route("/<dataset_id>/csv")
def download_csv(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if not dataset:
        return "Dataset not found", 404
    check_project_role(dataset.get("project_id"), "viewer")
    data = (dataset.get("data_csv") or "").encode("utf-8")
    safe_name = (dataset.get("name") or "dataset").replace(" ", "_")
    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{safe_name}.csv",
    )


def _delete_dataset(dataset_id):
    dataset = db.get("datasets", dataset_id)
    if dataset:
        check_project_role(dataset.get("project_id"), "collaborator")
    for a in db.query("analyses", "dataset_id = ?", (dataset_id,)):
        db.delete("analyses", a["id"])
    db.delete("datasets", dataset_id)


@bp.route("/api/dataset/<dataset_id>", methods=["DELETE"])
def api_delete(dataset_id):
    _delete_dataset(dataset_id)
    return jsonify({"ok": True})


@bp.route("/<dataset_id>/delete", methods=["POST"])
def delete_form(dataset_id):
    _delete_dataset(dataset_id)
    return redirect(url_for("datasets.index"))


# Heavy parsers imported at module load so route handlers stay simple. Kept at
# the bottom to keep the public surface (bp, store) obvious at the top.
import pandas as pd  # noqa: E402
import pyreadstat  # noqa: E402
