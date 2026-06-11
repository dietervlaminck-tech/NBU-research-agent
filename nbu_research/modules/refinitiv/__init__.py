"""LSEG Refinitiv connector.

Pulls market data, fundamentals, and ESG data from LSEG (Refinitiv) into a
`datasets` row (via datasets.store.from_dataframe), ready for analysis/export —
the same connector→dataset pattern proven by the SEC EDGAR module.

Two session modes (see docs/REFINITIV_DESIGN.md and config.py):
- desktop  — works on a machine running Refinitiv Workspace (the eikon2 seat).
- platform — server-capable (machine account); for the Azure deployment.

The connector is DORMANT until configured: when the lseg-data library is absent
or no app key is set, the UI shows a clear "not configured" card and never
errors. The data-fetch path is written against the documented lseg-data API but
is necessarily unverified live here (no library, no Workspace, no key in this
environment) — see client.py.
"""
from flask import (
    Blueprint, render_template, request, redirect, url_for,
)

from ... import db
from ...config import LSEG_SESSION, lseg_configured
from ...jobs import start_job, get_job, update_progress
from ..datasets.store import from_dataframe
from . import client

bp = Blueprint("refinitiv", __name__)

# Field bundles offered in the UI. Refinitiv field codes (TR.*) are entitlement-
# gated; these are common ones — the actual set available depends on the
# Nyenrode license (see the open questions in docs/REFINITIV_DESIGN.md).
FIELD_BUNDLES = {
    "fundamentals": {
        "label": "Fundamentals (annual)",
        "fields": ["TR.Revenue", "TR.NetIncome", "TR.TotalAssets",
                   "TR.TotalLiabilities", "TR.EBIT", "TR.GrossProfit"],
    },
    "valuation": {
        "label": "Valuation & market",
        "fields": ["TR.PriceClose", "TR.CompanyMarketCap", "TR.PE", "TR.PriceToBVPerShare"],
    },
    "esg": {
        "label": "ESG scores",
        "fields": ["TR.TRESGScore", "TR.EnvironmentPillarScore",
                   "TR.SocialPillarScore", "TR.GovernancePillarScore"],
    },
}
DEFAULT_BUNDLE = "fundamentals"


def _availability():
    """(ok, reason) — whether the connector can run, with a user-facing reason."""
    lib_ok, lib_reason = client.library_available()
    if not lib_ok:
        return False, lib_reason
    if not lseg_configured():
        return False, (
            "Refinitiv credentials are not configured. Set LSEG_APP_KEY "
            f"(session mode: {LSEG_SESSION})"
            + (" plus LSEG_CLIENT_ID/LSEG_CLIENT_SECRET for platform mode"
               if LSEG_SESSION == "platform" else
               " and make sure Refinitiv Workspace is running on this machine")
            + ". See docs/REFINITIV_DESIGN.md."
        )
    return True, ""


# --- panel job ---------------------------------------------------------------

def _run_panel_job(job_id, project_id, name, description, instruments, fields,
                   start, end):
    def progress(frac, message):
        update_progress(job_id, round(frac * 0.9, 3), message)

    df, notes = client.build_panel(instruments, fields, start, end, progress=progress)
    if df is None or df.empty:
        msg = "No data returned. " + ("; ".join(notes) if notes else "")
        raise ValueError(msg.strip() or "Refinitiv returned no data for this request.")

    update_progress(job_id, 0.92, "Storing dataset…")
    dataset_id = from_dataframe(
        project_id, name, df, source="refinitiv",
        source_meta={"instruments": instruments, "fields": fields,
                     "start": start, "end": end, "session": LSEG_SESSION,
                     "notes": notes},
        description=description,
    )
    update_progress(job_id, 1.0, f"Dataset ready ({len(df)} rows).")
    return {"dataset_id": dataset_id, "n_rows": int(len(df)), "notes": notes}


# --- routes ------------------------------------------------------------------

def _index(error=None, status=400):
    ok, reason = _availability()
    ctx = {
        "projects": db.query("projects"),
        "datasets": db.query("datasets", "source = ?", ("refinitiv",)),
        "bundles": FIELD_BUNDLES,
        "default_bundle": DEFAULT_BUNDLE,
        "configured": ok,
        "not_configured_reason": reason,
        "session_mode": LSEG_SESSION,
        "project_id": request.args.get("project", ""),
        "error": error,
    }
    if error:
        return render_template("refinitiv/index.html", **ctx), status
    return render_template("refinitiv/index.html", **ctx)


@bp.route("/")
def index():
    return _index()


@bp.route("/panel", methods=["POST"])
def panel():
    ok, reason = _availability()
    if not ok:
        return _index(error=reason)

    instruments = client.parse_instruments(request.form.get("instruments", ""))
    bundle_key = request.form.get("bundle", DEFAULT_BUNDLE)
    bundle = FIELD_BUNDLES.get(bundle_key, FIELD_BUNDLES[DEFAULT_BUNDLE])
    fields = bundle["fields"]
    if not instruments:
        return _index(error="Enter at least one instrument (RIC or ticker).")

    start = request.form.get("start", "").strip() or None
    end = request.form.get("end", "").strip() or None
    project_id = request.form.get("project_id", "").strip() or None
    name = request.form.get("name", "").strip() or (
        "Refinitiv: " + ", ".join(instruments[:5]) + ("…" if len(instruments) > 5 else "")
    )
    description = (
        f"LSEG Refinitiv {bundle['label'].lower()} for {len(instruments)} "
        f"instrument(s): {', '.join(instruments)}."
    )

    job_id = start_job(
        "refinitiv_panel",
        lambda jid: _run_panel_job(jid, project_id, name, description,
                                   instruments, fields, start, end),
        ref_table="datasets",
    )
    return redirect(url_for("refinitiv.job_progress", job_id=job_id))


@bp.route("/job/<job_id>")
def job_progress(job_id):
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    return render_template("refinitiv/job.html", job=job)


@bp.route("/job/<job_id>/done")
def job_done(job_id):
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    if job.get("status") == "done":
        dataset_id = (job.get("result") or {}).get("dataset_id")
        if dataset_id:
            return redirect(f"/datasets/{dataset_id}")
    return render_template("refinitiv/job.html", job=job)
