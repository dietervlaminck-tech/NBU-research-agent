"""Article writing agent pipeline.

Generation (run inside jobs.start_job):
1. Structure-architect outline                     (complete_json) -> outline_md
2. Draft-writer prose, one call per section        (complete)      -> content_md
3. Peer-reviewer memo                              (complete)      -> metadata["review_md"]

Revision: one complete() call with the current draft + researcher instructions +
review memo -> new content_md, status 'revised'.

System prompts are distilled from the academic-paper / academic-paper-reviewer
skills and live in nbu_research/prompts/; inline defaults below are the fallback.
"""
import json
import os

from ... import db
from ...jobs import update_progress
from ...llm import complete, complete_json

PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
)


def load_prompt(name, default):
    """Load a prompt pack from nbu_research/prompts/<name>.md, else the default."""
    try:
        with open(os.path.join(PROMPTS_DIR, name + ".md"), encoding="utf-8") as f:
            text = f.read().strip()
            return text or default
    except OSError:
        return default


DEFAULT_OUTLINE_SYSTEM = """You are the structure architect of an academic writing pipeline. Design a
section outline (4-7 top-level sections, the last one 'References') that fits the article type:
empirical -> IMRaD; literature_review -> thematic; conceptual -> theoretical analysis; methods ->
methods paper. Structure serves argument; every section must be supportable by the provided project
materials. For each section give 3-6 bullets covering purpose, key claims, supporting materials, and
the transition to the next section."""

DEFAULT_WRITER_SYSTEM = """You are the draft writer of an academic writing pipeline. Write scholarly prose
for exactly one section, following the outline. Formal academic register; evidence-grounded; in-text
citations in (Author, year) form drawn ONLY from the provided source list — never fabricate
references. Claims rest on cited sources or on the project's own study data. Open with the claim, not
throat-clearing; vary sentence length. Return only the section's markdown starting with its ##
heading."""

DEFAULT_REVIEWER_SYSTEM = """You are a simulated peer reviewer producing a pre-submission review memo.
Assess originality, methodological rigor, evidence sufficiency, argument coherence, and writing
quality. Check citations against the references for orphans or fabrication. Output markdown with:
Overall Assessment (with verdict), Strengths, Weaknesses (by severity, each with a suggested fix),
and Revision Priorities."""

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "minItems": 4,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Section heading"},
                    "bullets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Purpose, key claims, supporting materials, transition",
                    },
                },
                "required": ["title", "bullets"],
            },
        }
    },
    "required": ["sections"],
}


# --- context gathering --------------------------------------------------------

def _format_source(s):
    line = f"{s.get('authors') or 'Unknown'} ({s.get('year') or 'n.d.'}). {s.get('title')}."
    if s.get("venue"):
        line += f" {s['venue']}."
    if s.get("doi"):
        line += f" https://doi.org/{s['doi'].replace('https://doi.org/', '')}"
    elif s.get("url"):
        line += f" {s['url']}"
    return line


def _gather_context(article):
    """Collect project materials selected in the article's metadata.

    Returns (context_text, sources) where sources is the list of source rows the
    writer may cite.
    """
    metadata = article.get("metadata") or {}
    parts = []
    sources = []

    if article.get("project_id"):
        project = db.get("projects", article["project_id"])
        if project:
            parts.append(
                f"# Project\nTitle: {project['title']}\n"
                f"Description: {project['description']}\n"
                f"Research question: {project['research_question']}"
            )

    for review_id in metadata.get("review_ids", []):
        review = db.get("literature_reviews", review_id)
        if not review:
            continue
        if review.get("report_md"):
            parts.append(
                f"# Literature review report: {review['research_question']}\n"
                + review["report_md"][:20000]
            )
        sources.extend(db.query("sources", "review_id = ?", (review_id,)))

    for study_id in metadata.get("study_ids", []):
        study = db.get("studies", study_id)
        if not study:
            continue
        parts.append(
            f"# Study design: {study['title']}\n"
            f"Type: {study['study_type']}\n"
            f"Research question: {study['research_question']}\n"
            f"Instrument/config: {json.dumps(study.get('config') or {}, ensure_ascii=False)[:6000]}"
        )
        for analysis in db.query("analyses", "study_id = ? AND status = ?", (study_id, "done")):
            results = analysis.get("results") or {}
            block = (
                f"# Analysis ({analysis['kind']}): {analysis['title']}\n"
                f"Parameters: {json.dumps(analysis.get('params') or {}, ensure_ascii=False)[:2000]}\n"
            )
            if isinstance(results, dict) and results.get("report_md"):
                block += f"Report:\n{str(results['report_md'])[:12000]}\n"
                rest = {k: v for k, v in results.items() if k != "report_md"}
                if rest:
                    block += f"Other results: {json.dumps(rest, ensure_ascii=False, default=str)[:6000]}"
            else:
                block += f"Results: {json.dumps(results, ensure_ascii=False, default=str)[:8000]}"
            parts.append(block)

    # Fall back to project-level sources when no literature review was selected.
    if not sources and article.get("project_id"):
        sources = db.query("sources", "project_id = ?", (article["project_id"],))

    # Deduplicate sources by id.
    seen, unique_sources = set(), []
    for s in sources:
        if s["id"] not in seen:
            seen.add(s["id"])
            unique_sources.append(s)

    context = "\n\n".join(parts) if parts else "(No project materials were selected.)"
    return context, unique_sources


def _article_brief(article):
    metadata = article.get("metadata") or {}
    brief = f"Title: {article['title']}\nArticle type: {article['article_type']}"
    if metadata.get("style_note"):
        brief += f"\nTarget style / journal note: {metadata['style_note']}"
    return brief


def _outline_to_md(sections):
    lines = []
    for sec in sections:
        lines.append(f"## {sec['title']}")
        for bullet in sec.get("bullets", []):
            lines.append(f"- {bullet}")
        lines.append("")
    return "\n".join(lines).strip()


# --- generation pipeline --------------------------------------------------------

def run_article_generation(article_id, job_id):
    article = db.get("articles", article_id)
    if not article:
        raise ValueError(f"Article {article_id} not found")
    try:
        return _generate(article, job_id)
    except Exception:
        db.update("articles", article_id, {"status": "error"})
        raise


def _generate(article, job_id):
    article_id = article["id"]
    metadata = article.get("metadata") or {}
    brief = _article_brief(article)

    update_progress(job_id, 0.05, "Gathering project materials")
    context, sources = _gather_context(article)
    source_list = "\n".join(f"- {_format_source(s)}" for s in sources) or "(No sources available.)"

    # --- Phase 1: outline -------------------------------------------------------
    update_progress(job_id, 0.1, "Designing the article structure")
    outline = complete_json(
        system=load_prompt("article_outline", DEFAULT_OUTLINE_SYSTEM),
        prompt=(
            f"{brief}\n\n"
            f"Available sources ({len(sources)}):\n{source_list[:8000]}\n\n"
            f"Project materials:\n{context[:40000]}\n\n"
            "Design the outline."
        ),
        schema=OUTLINE_SCHEMA,
    )
    sections = outline["sections"]
    outline_md = _outline_to_md(sections)
    db.update("articles", article_id, {"outline_md": outline_md})

    # --- Phase 2: section-by-section drafting ------------------------------------
    writer_system = load_prompt("article_writer", DEFAULT_WRITER_SYSTEM)
    drafted = []
    n = len(sections)
    for i, section in enumerate(sections):
        update_progress(job_id, 0.2 + 0.6 * i / n, f"Drafting section {i + 1}/{n}: {section['title']}")
        is_last = i == n - 1
        prior = "\n\n".join(drafted)
        if len(prior) > 16000:
            prior = "…(earlier sections truncated)…\n" + prior[-16000:]
        bullets = "\n".join(f"- {b}" for b in section.get("bullets", []))
        if is_last:
            task = (
                f"Now write the FINAL section: \"{section['title']}\". If this is the References "
                "section, list the full reference for every source cited in-text in the sections "
                "written so far — and only those."
            )
        else:
            task = (
                f"Now write ONLY the section: \"{section['title']}\".\n"
                f"Section plan:\n{bullets}\n"
                "Do not include a references list in this section."
            )
        text = complete(
            system=writer_system,
            prompt=(
                f"{brief}\n\n"
                f"Source list (the ONLY citable sources):\n{source_list[:10000]}\n\n"
                f"Project materials (evidence base):\n{context[:30000]}\n\n"
                f"Full outline of the article:\n{outline_md}\n\n"
                f"Sections written so far:\n{prior or '(none yet)'}\n\n"
                f"{task}"
            ),
        )
        drafted.append(text.strip())
        db.update("articles", article_id, {"content_md": "\n\n".join(drafted)})

    content_md = f"# {article['title']}\n\n" + "\n\n".join(drafted)
    db.update("articles", article_id, {"content_md": content_md})

    # --- Phase 3: peer review memo ------------------------------------------------
    update_progress(job_id, 0.9, "Running the simulated peer review")
    review_md = complete(
        system=load_prompt("article_reviewer", DEFAULT_REVIEWER_SYSTEM),
        prompt=(
            f"{brief}\n\n"
            f"Source list the author was allowed to cite:\n{source_list[:8000]}\n\n"
            f"Full draft to review:\n\n{content_md[:80000]}\n\n"
            "Write the review memo."
        ),
    )
    metadata = (db.get("articles", article_id) or {}).get("metadata") or metadata
    metadata["review_md"] = review_md
    db.update("articles", article_id, {"metadata": metadata, "status": "draft"})
    return {"article_id": article_id, "sections": n, "sources": len(sources)}


# --- revision pipeline -----------------------------------------------------------

def run_article_revision(article_id, instructions, job_id):
    article = db.get("articles", article_id)
    if not article:
        raise ValueError(f"Article {article_id} not found")
    try:
        return _revise(article, instructions, job_id)
    except Exception:
        db.update("articles", article_id, {"status": "error"})
        raise


def _revise(article, instructions, job_id):
    article_id = article["id"]
    metadata = article.get("metadata") or {}
    brief = _article_brief(article)
    _, sources = _gather_context(article)
    source_list = "\n".join(f"- {_format_source(s)}" for s in sources) or "(No sources available.)"
    review_md = metadata.get("review_md", "")

    update_progress(job_id, 0.3, "Revising the draft")
    revised = complete(
        system=load_prompt("article_writer", DEFAULT_WRITER_SYSTEM)
        + "\n\nYou are now in REVISION mode: rewrite the complete article, applying the "
        "researcher's revision instructions (highest priority) and the peer review memo where "
        "compatible with them. Keep what already works; do not change citations to sources "
        "outside the source list. Return the complete revised article as markdown, starting "
        "with the # title heading, including the References section.",
        prompt=(
            f"{brief}\n\n"
            f"Source list (the ONLY citable sources):\n{source_list[:10000]}\n\n"
            f"Peer review memo:\n{review_md[:15000] or '(none)'}\n\n"
            f"Researcher's revision instructions:\n{instructions}\n\n"
            f"Current draft:\n\n{article['content_md'][:90000]}\n\n"
            "Produce the full revised article."
        ),
        max_tokens=32000,
    )
    db.update("articles", article_id, {"content_md": revised, "status": "revised"})
    return {"article_id": article_id}
