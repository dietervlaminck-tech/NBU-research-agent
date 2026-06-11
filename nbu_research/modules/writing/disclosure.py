"""AI disclosure statement generation.

Builds a journal-ready disclosure of how AI assisted an article, grounded in
the platform's own records: the article's generation/revision history (articles
metadata + jobs rows) and the central ai_usage_log audit trail.
"""
import json

from ... import db, llm

DISCLOSURE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "models_used": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "what_it_did": {"type": "string"},
                },
                "required": ["model", "what_it_did"],
            },
        },
        "human_oversight_statement": {"type": "string"},
        "recommended_journal_format": {
            "type": "object",
            "properties": {
                "apa_author_note": {"type": "string"},
                "springer_data_availability": {"type": "string"},
                "generic": {"type": "string"},
            },
            "required": ["apa_author_note", "springer_data_availability", "generic"],
        },
    },
    "required": ["summary", "models_used", "human_oversight_statement",
                 "recommended_journal_format"],
}

SYSTEM = (
    "You write AI-contribution disclosure statements for academic manuscripts, "
    "following emerging journal and COPE norms. Be factual and specific: state "
    "what AI systems did, what they did not do, and that the researchers "
    "verified outputs and bear full responsibility for the content. Plain "
    "English, no marketing language, no hedging beyond what the evidence "
    "supports. Never overstate or understate the AI's role relative to the "
    "provided usage records."
)


def usage_for_article(article_id):
    """(jobs, log_rows, date_range) backing this article's AI history."""
    jobs = db.query("jobs", "ref_table = 'articles' AND ref_id = ?",
                    (article_id,), order="created_at ASC")
    job_ids = [j["id"] for j in jobs]
    log_rows = []
    if job_ids:
        marks = ",".join("?" for _ in job_ids)
        log_rows = db.query("ai_usage_log", f"job_id IN ({marks})",
                            tuple(job_ids), order="timestamp ASC")
    dates = [r["timestamp"][:10] for r in log_rows] or \
            [j["created_at"][:10] for j in jobs]
    date_range = (min(dates), max(dates)) if dates else (None, None)
    return jobs, log_rows, date_range


def generate_disclosure(article):
    """One structured disclosure object for an article. Raises RuntimeError
    when no API key is configured (caller shows a friendly message)."""
    jobs, log_rows, date_range = usage_for_article(article["id"])
    metadata = article.get("metadata") or {}

    history = {
        "article_title": article.get("title", ""),
        "article_type": article.get("article_type", ""),
        "status": article.get("status", ""),
        "created_at": article.get("created_at", ""),
        "updated_at": article.get("updated_at", ""),
        "outline_generated": bool(article.get("outline_md")),
        "peer_review_memo_generated": bool(metadata.get("review_md")),
        "revision_instructions_given": metadata.get("revision_instructions"),
        "pipeline_jobs": [
            {"kind": j["kind"], "status": j["status"], "created_at": j["created_at"]}
            for j in jobs
        ],
        "models_used": sorted({r["model"] for r in log_rows}),
        "ai_calls_logged": len(log_rows),
        "total_tokens_approx": sum(r.get("token_count_approx") or 0 for r in log_rows),
        "ai_assistance_date_range": {"from": date_range[0], "to": date_range[1]},
    }

    prompt = (
        "Write the AI disclosure for this manuscript draft produced with the "
        "NBU Research Agent platform (Nyenrode Business Universiteit). The "
        "platform drafted the outline and prose section-by-section from "
        "researcher-provided sources and results, generated an internal "
        "peer-review memo, and applied researcher-directed revisions. The "
        "researcher supplied the research question, data, and sources, and "
        "reviews/edits all output.\n\n"
        "Usage records (factual basis — do not invent beyond this):\n"
        f"{json.dumps(history, indent=2)}\n\n"
        "Produce:\n"
        "- summary: one plain-English paragraph for a Methods section or "
        "author note.\n"
        "- models_used: each model with what it did in this manuscript.\n"
        "- human_oversight_statement: the researchers' verification "
        "responsibility, stated in a form authors can sign.\n"
        "- recommended_journal_format: the summary adapted to (1) an APA-style "
        "author note, (2) a Springer Nature-style declaration, (3) a generic "
        "open-ended disclosure."
    )
    result = llm.complete_json(SYSTEM, prompt, DISCLOSURE_SCHEMA, max_tokens=4000)
    result["_history"] = history
    return result
