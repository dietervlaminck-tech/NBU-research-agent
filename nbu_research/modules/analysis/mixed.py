"""Mixed-methods integration analysis (v0.2).

Links the THEMES of a completed thematic/deductive analysis (interviews) to the
CONSTRUCTS/variables of a survey or dataset in the same project, producing the
classic mixed-methods *joint display*: a themes × constructs matrix where each
cell records convergence, divergence, complementarity, or silence — plus an AI
meta-inference report.

Numbers come from Python (the quantitative descriptives), theme summaries from
the stored qualitative results; the LLM only relates the two and writes prose.
"""
import json

from ... import db, llm
from ...config import DEFAULT_PIPELINE_MODEL
from ...jobs import job, start_job, update_progress
from . import quantitative

KIND = "mixed_methods"

_RELATIONS = ("converges", "diverges", "complements", "silent")

_MATRIX_SCHEMA = {
    "type": "object",
    "properties": {
        "cells": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "construct": {"type": "string"},
                    "relation": {"type": "string", "enum": list(_RELATIONS)},
                    "note": {"type": "string"},
                },
                "required": ["theme", "construct", "relation", "note"],
            },
        },
    },
    "required": ["cells"],
}


def _themes_from(analysis):
    """Theme labels from a thematic/deductive analysis: parent codes when the
    codebook has a hierarchy, otherwise the top codes by frequency."""
    results = analysis.get("results") or {}
    codebook = db.get("codebooks", results.get("codebook_id") or "") or {}
    codes = codebook.get("codes") or []
    by_id = {c.get("id"): c for c in codes}
    parents = [c for c in codes if not c.get("parent_id")]
    children_exist = any(c.get("parent_id") for c in codes)
    if parents and children_exist:
        return [{"id": c["id"], "name": c.get("name", c["id"])} for c in parents]
    counts = results.get("code_counts") or []
    top = sorted(counts, key=lambda c: -(c.get("count") or 0))[:10]
    return [{"id": c.get("code_id"), "name": c.get("name", c.get("code_id"))}
            for c in top if by_id.get(c.get("code_id"))] or \
           [{"id": c.get("id"), "name": c.get("name", c.get("id"))} for c in codes[:10]]


def _constructs_from(target):
    """Constructs for the quantitative side: construct-tagged survey scales
    when present, otherwise the numeric variables themselves (capped)."""
    cols = quantitative.dataframe_columns(target)
    numeric = [c for c in cols if c["kind"] == "numeric"]
    groups = {}
    if not quantitative.is_dataset(target):
        for q in (target.get("config") or {}).get("questions") or []:
            tag = q.get("construct")
            if tag:
                groups.setdefault(tag, []).append(q.get("id"))
    if groups:
        return [{"name": tag, "items": items} for tag, items in groups.items()]
    return [{"name": c["label"] or c["id"], "items": [c["id"]]} for c in numeric[:10]]


def _quant_summary(target, constructs):
    """Per-construct descriptives, computed in Python."""
    df = quantitative.responses_dataframe(target)
    out = []
    for con in constructs:
        items = [i for i in con["items"] if i in df.columns]
        if not items:
            continue
        sub = df[items].apply(lambda s: s.astype(float, errors="ignore"), axis=0)
        try:
            mean_of_mean = float(sub.mean(numeric_only=True).mean())
            sd = float(sub.std(numeric_only=True, ddof=1).mean())
        except Exception:
            mean_of_mean, sd = None, None
        out.append({"construct": con["name"], "n_items": len(items),
                    "mean": None if mean_of_mean != mean_of_mean else round(mean_of_mean, 3),
                    "sd": None if sd != sd else round(sd, 3),
                    "n": int(len(df))})
    return out


@job(KIND)
def _run_mixed_job(job_id, project_id=None, thematic_analysis_id=None,
                   quant_kind=None, quant_id=None):
    qual = db.get("analyses", thematic_analysis_id)
    if not qual or qual.get("status") != "done":
        raise ValueError("Pick a completed thematic or deductive analysis.")
    target = db.get("studies" if quant_kind == "study" else "datasets", quant_id)
    if not target:
        raise ValueError("Quantitative study/dataset not found.")

    update_progress(job_id, 0.1, "Assembling themes and constructs")
    themes = _themes_from(qual)
    constructs = _constructs_from(target)
    if not themes:
        raise ValueError("The selected analysis has no themes/codes.")
    if not constructs:
        raise ValueError("The selected study/dataset has no numeric constructs.")
    quant_stats = _quant_summary(target, constructs)
    qual_report = (qual.get("results") or {}).get("report_md", "")[:20000]

    update_progress(job_id, 0.35, "Relating themes to constructs (joint display)")
    system = (
        "You are a mixed-methods methodologist building a joint display. For "
        "every (theme, construct) pair, judge the relation strictly from the "
        "evidence given: 'converges' (both point the same way), 'diverges' "
        "(they contradict), 'complements' (qualitative explains or extends the "
        "quantitative picture), or 'silent' (no meaningful link). Be sparing "
        "with 'converges'/'diverges' — claim them only when the evidence is "
        "explicit. Each note is one tight sentence grounded in the inputs."
    )
    prompt = (
        f"THEMES (qualitative):\n{json.dumps([t['name'] for t in themes])}\n\n"
        f"QUALITATIVE REPORT (source of theme evidence):\n{qual_report}\n\n"
        f"CONSTRUCTS with descriptives (quantitative):\n"
        f"{json.dumps(quant_stats, indent=1)}\n\n"
        f"Produce a cell for EVERY theme × construct pair."
    )
    matrix = llm.complete_json(system, prompt, _MATRIX_SCHEMA,
                               model=DEFAULT_PIPELINE_MODEL, max_tokens=16000)
    cells = matrix.get("cells") or []

    update_progress(job_id, 0.75, "Writing meta-inference report")
    report = llm.complete(
        "You are a mixed-methods methodologist writing the integration section "
        "of an empirical paper. From the joint display and inputs, write a "
        "markdown report: ## Joint display summary, ## Meta-inferences (where "
        "strands converge/diverge and what that means), ## Divergence "
        "resolution (how to explain or further probe contradictions), "
        "## Limitations of this integration (different samples/levels, AI-"
        "assisted coding, construct coverage). Ground every claim in the "
        "provided evidence; no fabricated statistics.",
        f"JOINT DISPLAY CELLS:\n{json.dumps(cells, indent=1)}\n\n"
        f"QUANT DESCRIPTIVES:\n{json.dumps(quant_stats, indent=1)}\n\n"
        f"QUAL REPORT:\n{qual_report}",
        model=DEFAULT_PIPELINE_MODEL, max_tokens=8000,
    )

    update_progress(job_id, 0.95, "Storing analysis")
    analysis_id = db.insert("analyses", {
        "kind": KIND,
        "project_id": project_id,
        "study_id": quant_id if quant_kind == "study" else None,
        "dataset_id": quant_id if quant_kind == "dataset" else None,
        "title": f"Mixed-methods integration — {qual.get('title', '')[:60]}",
        "params": {"thematic_analysis_id": thematic_analysis_id,
                   "quant_kind": quant_kind, "quant_id": quant_id},
        "results": {
            "report_md": report,
            "cells": cells,
            "themes": themes,
            "constructs": [c["name"] for c in constructs],
            "quant_stats": quant_stats,
            "base_analysis_id": thematic_analysis_id,
        },
        "status": "done",
        "completed_at": db.now(),
    })
    update_progress(job_id, 1.0, "Completed")
    return {"analysis_id": analysis_id}


def start_mixed_job(project_id, thematic_analysis_id, quant_kind, quant_id):
    return start_job(KIND, {
        "project_id": project_id,
        "thematic_analysis_id": thematic_analysis_id,
        "quant_kind": quant_kind,
        "quant_id": quant_id,
    }, ref_table="analyses")
