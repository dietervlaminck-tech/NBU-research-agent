"""Literature review agent pipeline.

Phases (run inside jobs.start_job):
1. Decompose the research question into search angles   (complete_json)
2. Web-search-grounded research per angle                (research)
3. Extract a structured source list                      (complete_json)
4. Synthesize the literature review report               (complete)

System prompts are distilled from the deep-research skill methodology and live in
nbu_research/prompts/; inline defaults below are the fallback.
"""
import os

from ... import db
from ...jobs import job, update_progress
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...config import MODEL_SONNET
from ...llm import complete, complete_json, research

# Per-angle retrieval favours speed (Sonnet); final synthesis uses the default
# pipeline model (Opus). Web-search retrieval doesn't need Opus-level reasoning.
RESEARCH_MODEL = MODEL_SONNET

DEPTH_SEARCHES = {"quick": 3, "standard": 5, "deep": 8}

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


DEFAULT_RESEARCH_SYSTEM = """You are an academic literature researcher executing one search angle of a
systematic literature review. Prioritise peer-reviewed sources; balance seminal works with recent
publications; for every source record authors, year, title, venue, DOI/URL, the methodology used,
and the key findings. Never fabricate references — if you cannot confirm a source exists, exclude it.
Report contradictions between sources rather than cherry-picking. Produce structured research notes:
overview, key sources (with method, findings, relevance), tensions, and gaps."""

DEFAULT_SYNTHESIS_SYSTEM = """You are the synthesis author of a literature review pipeline. Integrate research
notes from several search angles into one scholarly markdown report with sections: Introduction,
Themes in the Literature, Theoretical Framings, Methodological Landscape, Gaps in the Literature,
Implications for the Research Question, Limitations of this Review, References. Every claim carries
an (Author, year) citation drawn ONLY from the provided sources; report disagreements between
sources; never invent references."""

ANGLES_SCHEMA = {
    "type": "object",
    "properties": {
        "angles": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name for this search angle"},
                    "query": {"type": "string", "description": "The research sub-question this angle pursues"},
                    "rationale": {"type": "string", "description": "Why this angle matters for the research question"},
                },
                "required": ["title", "query", "rationale"],
            },
        }
    },
    "required": ["angles"],
}

SOURCES_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "authors": {"type": "string", "description": "e.g. 'Smith, J., & Lee, K.'"},
                    "year": {"type": "string"},
                    "venue": {"type": "string", "description": "Journal, publisher, or conference"},
                    "url": {"type": "string"},
                    "doi": {"type": "string"},
                    "abstract": {"type": "string", "description": "1-2 sentence summary of the source"},
                    "notes": {"type": "string", "description": "Key findings and relevance to the research question"},
                },
                "required": ["title", "authors", "year", "venue", "url", "doi", "abstract", "notes"],
            },
        }
    },
    "required": ["sources"],
}


def _scope_text(scope):
    parts = []
    if scope.get("discipline"):
        parts.append(f"Discipline: {scope['discipline']}")
    if scope.get("year_from") or scope.get("year_to"):
        parts.append(f"Year range: {scope.get('year_from') or 'any'}–{scope.get('year_to') or 'present'}")
    if scope.get("source_types"):
        parts.append(f"Preferred source types: {scope['source_types']}")
    parts.append(f"Depth: {scope.get('depth', 'standard')}")
    return "\n".join(parts)


def run_literature_review(review_id, job_id):
    review = db.get("literature_reviews", review_id)
    if not review:
        raise ValueError(f"Literature review {review_id} not found")
    try:
        return _run(review, job_id)
    except Exception:
        db.update("literature_reviews", review_id, {"status": "error"})
        raise


def _run(review, job_id):
    review_id = review["id"]
    scope = review.get("scope") or {}
    question = review["research_question"]
    scope_text = _scope_text(scope)
    max_searches = DEPTH_SEARCHES.get(scope.get("depth", "standard"), 10)

    db.update("literature_reviews", review_id, {"status": "running"})

    # --- Phase 1: decompose into search angles --------------------------------
    update_progress(job_id, 0.1, "Decomposing the research question into search angles")
    decomposition = complete_json(
        system=(
            "You are the search strategist of a systematic literature review. Decompose the "
            "research question into 3-6 complementary search angles that together cover the "
            "conceptual, theoretical, methodological, and empirical territory of the question. "
            "Angles must be distinct (minimal overlap) and each answerable through academic "
            "literature search."
        ),
        prompt=(
            f"Research question:\n{question}\n\nScope:\n{scope_text}\n\n"
            "Produce the search angles."
        ),
        schema=ANGLES_SCHEMA,
    )
    angles = decomposition["angles"][:6]

    # --- Phase 2: web-search-grounded research per angle -----------------------
    # Angles are independent, so research them CONCURRENTLY (each call is a
    # multi-minute web-search round trip — running 6 sequentially was the main
    # cause of 30-50 min reviews). Retrieval runs on Sonnet for speed; the
    # final synthesis (Phase 4) stays on the default pipeline model.
    research_system = load_prompt("literature_review", DEFAULT_RESEARCH_SYSTEM)
    n = len(angles)

    def _research_angle(angle):
        return research(
            system=research_system,
            prompt=(
                f"Overall research question of the review:\n{question}\n\n"
                f"Scope set by the researcher:\n{scope_text}\n\n"
                f"Your assigned search angle: {angle['title']}\n"
                f"Sub-question: {angle['query']}\n"
                f"Rationale: {angle['rationale']}\n\n"
                "Research this angle and produce your structured research notes."
            ),
            model=RESEARCH_MODEL,
            max_searches=max_searches,
        )

    update_progress(job_id, 0.2, f"Researching {n} angles in parallel…")
    angle_notes = [None] * n
    done = {"count": 0}
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=min(n, 5)) as pool:
        futures = {pool.submit(_research_angle, angle): i
                   for i, angle in enumerate(angles)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                text, citations = fut.result()
            except Exception as e:  # one angle failing must not kill the review
                text, citations = f"(Angle research failed: {e})", []
            angle_notes[i] = {"angle": angles[i], "text": text, "citations": citations}
            with lock:
                done["count"] += 1
                update_progress(
                    job_id, 0.2 + 0.5 * done["count"] / n,
                    f"Researched {done['count']}/{n} angles "
                    f"(latest: {angles[i]['title']})")

    all_citations = []
    for an in angle_notes:
        for c in an["citations"]:
            if c["url"] not in [x["url"] for x in all_citations]:
                all_citations.append(c)

    notes_blob = "\n\n".join(
        f"## Angle {i + 1}: {an['angle']['title']}\n{an['text'][:18000]}"
        for i, an in enumerate(angle_notes)
    )
    citations_blob = "\n".join(f"- {c['title'] or '(untitled)'} — {c['url']}" for c in all_citations)

    # --- Phase 3: extract structured source list -------------------------------
    update_progress(job_id, 0.8, "Extracting the structured source list")
    extraction = complete_json(
        system=(
            "You extract a deduplicated, structured source list from literature research notes. "
            "Include only sources that actually appear in the notes or the collected web "
            "citations — never invent or complete bibliographic details you cannot see. Use an "
            "empty string for unknown fields. Merge duplicate mentions of the same work."
        ),
        prompt=(
            f"Research question:\n{question}\n\n"
            f"Research notes per angle:\n{notes_blob}\n\n"
            f"Web citations collected during search:\n{citations_blob}\n\n"
            "Extract the structured source list."
        ),
        schema=SOURCES_SCHEMA,
    )
    sources = extraction["sources"]
    for s in sources:
        db.insert("sources", {
            "review_id": review_id,
            "project_id": review["project_id"],
            "title": (s.get("title") or "").strip() or "(untitled)",
            "authors": s.get("authors") or "",
            "year": s.get("year") or "",
            "venue": s.get("venue") or "",
            "url": s.get("url") or "",
            "doi": s.get("doi") or "",
            "abstract": s.get("abstract") or "",
            "notes": s.get("notes") or "",
        })

    # --- Phase 4: synthesize the report ----------------------------------------
    update_progress(job_id, 0.9, "Synthesizing the literature review report")
    source_list = "\n".join(
        f"- {s.get('authors') or 'Unknown'} ({s.get('year') or 'n.d.'}). {s.get('title')}. "
        f"{s.get('venue')}. {s.get('doi') or s.get('url') or ''}".strip()
        for s in sources
    )
    report_md = complete(
        system=load_prompt("literature_synthesis", DEFAULT_SYNTHESIS_SYSTEM),
        prompt=(
            f"Research question:\n{question}\n\n"
            f"Scope:\n{scope_text}\n\n"
            f"Source list (the ONLY sources you may cite):\n{source_list}\n\n"
            f"Research notes per angle:\n{notes_blob}\n\n"
            "Write the full literature review report in markdown."
        ),
    )

    db.update("literature_reviews", review_id, {
        "report_md": report_md,
        "status": "done",
        "completed_at": db.now(),
    })
    return {"review_id": review_id, "angles": len(angles), "sources": len(sources)}


@job("literature_review")
def _literature_review_job(job_id, review_id=None):
    return run_literature_review(review_id, job_id)


# --- Re-synthesis (v0.3): synthesis phase only, grounded in PDF fulltext ------

FULLTEXT_EXCERPT_CHARS = 8000


def _source_line(s):
    return (
        f"- {s.get('authors') or 'Unknown'} ({s.get('year') or 'n.d.'}). {s.get('title')}. "
        f"{s.get('venue') or ''}. {s.get('doi') or s.get('url') or ''}"
    ).strip()


def run_resynthesize(review_id, job_id):
    """Re-run ONLY the synthesis phase over the stored sources table, adding
    labelled excerpts of ingested PDF fulltext as grounding material."""
    review = db.get("literature_reviews", review_id)
    if not review:
        raise ValueError(f"Literature review {review_id} not found")
    sources = db.query(
        "sources", "review_id = ?", (review_id,), order="year DESC, title ASC"
    )
    if not sources:
        raise ValueError("This review has no sources to synthesize from")

    question = review["research_question"]
    scope_text = _scope_text(review.get("scope") or {})
    source_list = "\n".join(_source_line(s) for s in sources)

    excerpts = []
    for s in sources:
        fulltext = (s.get("fulltext") or "").strip()
        if not fulltext:
            continue
        excerpts.append(
            f"### Full-text excerpt — {s.get('authors') or 'Unknown'} "
            f"({s.get('year') or 'n.d.'}). {s.get('title')}\n"
            f"{fulltext[:FULLTEXT_EXCERPT_CHARS]}"
        )
    excerpt_blob = "\n\n".join(excerpts) if excerpts else "(no full texts ingested)"

    update_progress(
        job_id, 0.3,
        f"Re-synthesizing the report from {len(sources)} sources "
        f"({len(excerpts)} with full text)",
    )
    report_md = complete(
        system=load_prompt("literature_synthesis", DEFAULT_SYNTHESIS_SYSTEM),
        prompt=(
            f"Research question:\n{question}\n\n"
            f"Scope:\n{scope_text}\n\n"
            f"Source list (the ONLY sources you may cite):\n{source_list}\n\n"
            "Full-text excerpts from ingested PDFs (primary grounding material — "
            "prefer these over your priors when they conflict; each excerpt is "
            "labelled with its source):\n"
            f"{excerpt_blob}\n\n"
            "Write the full literature review report in markdown."
        ),
    )

    db.update("literature_reviews", review_id, {
        "report_md": report_md,
        "status": "done",
        "completed_at": db.now(),
    })
    return {"review_id": review_id}


@job("resynthesize_review")
def _resynthesize_review_job(job_id, review_id=None):
    return run_resynthesize(review_id, job_id)
