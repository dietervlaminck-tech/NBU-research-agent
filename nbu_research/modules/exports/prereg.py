"""Preregistration package (.zip) for OSF / AsPredicted-style workflows.

Bundles everything a project has committed to so far: research question and
description, the methods-advisor review, the planned instruments (interview
outlines, survey question lists), analyses run to date (labeled honestly as
such — this platform cannot know analyses that were merely intended), the
sources collected in literature reviews, plus raw instrument dumps and
codebook JSONs as separate files.
"""
import io
import json
import zipfile
from datetime import date

from ... import db
from .common import slugify
from .documents import _source_line


# --- instrument rendering ------------------------------------------------------

def _scale_text(scale):
    if not scale:
        return ""
    rng = f"{scale.get('min', '?')}-{scale.get('max', '?')}"
    if scale.get("min_label") or scale.get("max_label"):
        rng += f" ({scale.get('min_label', '')} .. {scale.get('max_label', '')})"
    return rng


def _question_line(q, index):
    qid = q.get("id") or f"q{index + 1}"
    qtype = q.get("type", "open")
    detail = []
    scale = q.get("scale") or {}
    if scale and qtype in ("likert", "numeric", "matrix"):
        detail.append(f"scale {_scale_text(scale)}")
    if q.get("options"):
        detail.append("options: " + " / ".join(str(o) for o in q["options"]))
    if q.get("rows"):
        detail.append("rows: " + " / ".join(str(r) for r in q["rows"]))
    if q.get("required"):
        detail.append("required")
    suffix = f" [{'; '.join(detail)}]" if detail else ""
    return f"- **{qid}** ({qtype}): {q.get('text', '')}{suffix}"


def instrument_md(study):
    """Markdown dump of one study's collection instrument."""
    config = study.get("config") or {}
    lines = [f"# {study.get('title') or 'Untitled study'}", "",
             f"Study type: {study.get('study_type', '')}"]
    if study.get("research_question"):
        lines += ["", f"Research question: {study['research_question']}"]
    lines.append("")
    if study.get("study_type") == "interview":
        lines += ["## Interview outline", "",
                  config.get("interview_outline") or "(no outline defined)"]
        if config.get("general_instructions"):
            lines += ["", "## General instructions", "",
                      config["general_instructions"]]
    else:
        if config.get("welcome_text"):
            lines += ["## Welcome text", "", config["welcome_text"], ""]
        questions = config.get("questions") or []
        lines += ["## Questions", ""]
        if questions:
            lines += [_question_line(q, i) for i, q in enumerate(questions)]
        else:
            lines.append("(no questions defined)")
        citations = config.get("scale_citations") or {}
        if citations:
            lines += ["", "## Instrument citations", ""]
            lines += [f"- {k}: {v}" for k, v in citations.items()]
    return "\n".join(lines) + "\n"


# --- prereg.md sections ----------------------------------------------------------

def _methods_check_section(project):
    record = project.get("methods_check_json") or {}
    result = record.get("result") or {}
    if not result:
        return ["## Methods advisor review", "",
                "No methods-advisor review has been run for this project.", ""]
    lines = ["## Methods advisor review", ""]
    checked = (record.get("checked_at") or "")[:10]
    lines.append(f"Overall assessment: **{result.get('overall_assessment', '')}**"
                 + (f" (checked {checked})" if checked else ""))
    lines.append("")
    flags = result.get("flags") or []
    if flags:
        for f in flags:
            lines.append(f"- **{f.get('severity', '')}** — {f.get('category', '')}: "
                         f"{f.get('explanation', '')} "
                         f"Suggestion: {f.get('suggestion', '')}")
    else:
        lines.append("- No flags raised.")
    resources = result.get("recommended_resources") or []
    if resources:
        lines += ["", "Recommended resources:", ""]
        lines += [f"- {r.get('title', '')} — {r.get('reason', '')}"
                  for r in resources]
    lines.append("")
    return lines


def _instruments_section(studies):
    lines = ["## Planned instruments", ""]
    if not studies:
        return lines + ["No data-collection instruments defined yet.", ""]
    for study in studies:
        config = study.get("config") or {}
        lines += [f"### {study.get('title') or 'Untitled study'} "
                  f"({study.get('study_type', '')})", ""]
        if study.get("study_type") == "interview":
            lines += [config.get("interview_outline") or "(no outline defined)", ""]
        else:
            questions = config.get("questions") or []
            if questions:
                lines += [_question_line(q, i) for i, q in enumerate(questions)]
            else:
                lines.append("(no questions defined)")
            lines.append("")
    return lines


def _analyses_section(analyses):
    lines = ["## Planned analyses", "",
             "The list below reports the analyses run on this platform so far "
             "(\"analyses to date\") — it is a factual record, not a forward-"
             "looking analysis plan. Add intended analyses not yet run when "
             "submitting the preregistration.", ""]
    if analyses:
        for a in analyses:
            when = (a.get("created_at") or "")[:10]
            lines.append(f"- {a.get('kind', '')} — {a.get('title', '')}"
                         + (f" ({when})" if when else ""))
    else:
        lines.append("- No analyses run to date.")
    lines.append("")
    return lines


def _sources_section(reviews, sources):
    lines = ["## Sources overview", ""]
    lines.append(f"{len(sources)} source(s) collected across "
                 f"{len(reviews)} literature review(s).")
    lines.append("")
    lines += [f"- {_source_line(s)}" for s in sources]
    if sources:
        lines.append("")
    return lines


# --- package assembly -------------------------------------------------------------

def _project_rows(project_id):
    studies = db.query("studies", "project_id = ?", (project_id,),
                       order="created_at ASC")
    study_ids = {s["id"] for s in studies}
    dataset_ids = {d["id"] for d in
                   db.query("datasets", "project_id = ?", (project_id,))}
    analyses = [a for a in db.query("analyses", order="created_at ASC")
                if a.get("project_id") == project_id
                or a.get("study_id") in study_ids
                or a.get("dataset_id") in dataset_ids]
    reviews = db.query("literature_reviews", "project_id = ?", (project_id,))
    sources = []
    for r in reviews:
        sources += db.query("sources", "review_id = ?", (r["id"],),
                            order="created_at ASC")
    return studies, analyses, reviews, sources


def project_prereg(project_id):
    """Build the preregistration .zip for a project (bytes, filename, mime)."""
    project = db.get("projects", project_id) or {}
    studies, analyses, reviews, sources = _project_rows(project_id)
    today = date.today().isoformat()

    lines = [f"# Preregistration: {project.get('title') or 'Untitled project'}", "",
             f"Generated {today} by NBU Research Agent.", "",
             "## Research question", "",
             project.get("research_question") or "(not stated)", "",
             "## Project description", "",
             project.get("description") or "(no description)", ""]
    lines += _methods_check_section(project)
    lines += _instruments_section(studies)
    lines += _analyses_section(analyses)
    lines += _sources_section(reviews, sources)
    prereg_md = "\n".join(lines)

    readme = "\n".join([
        "# Preregistration package", "",
        f"Generated {today} by NBU Research Agent for project "
        f"\"{project.get('title', '')}\".", "",
        "Contents:", "",
        "- `prereg.md` — the preregistration document: research question,",
        "  description, methods-advisor review, planned instruments, analyses",
        "  to date, and sources overview.",
        "- `instruments/` — one markdown file per study with the full",
        "  collection instrument (interview outline or survey questions).",
        "- `codebooks/` — one JSON file per codebook attached to the",
        "  project's interview studies.", "",
        "Upload this package to an OSF project or attach it to an",
        "AsPredicted-style preregistration.", "",
    ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", readme)
        zf.writestr("prereg.md", prereg_md)
        for i, study in enumerate(studies, 1):
            name = slugify(study.get("title"), "study")
            zf.writestr(f"instruments/{i:02d}-{name}.md", instrument_md(study))
        n = 0
        for study in studies:
            if study.get("study_type") != "interview":
                continue
            for cb in db.query("codebooks", "study_id = ?", (study["id"],),
                               order="created_at ASC"):
                n += 1
                payload = {"name": cb.get("name", ""),
                           "study": study.get("title", ""),
                           "codes": cb.get("codes") or []}
                zf.writestr(
                    f"codebooks/{n:02d}-{slugify(cb.get('name'), 'codebook')}.json",
                    json.dumps(payload, indent=2, ensure_ascii=False))

    filename = f"{slugify(project.get('title'), 'project')}-prereg.zip"
    return buf.getvalue(), filename, "application/zip"
