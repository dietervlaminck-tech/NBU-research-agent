"""AI usage logging + disclosure data assembly (Feature 3)."""
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db, llm  # noqa: E402

db.init_db()

from nbu_research.modules.writing import disclosure  # noqa: E402


def test_log_usage_writes_row_with_real_usage():
    before = len(db.query("ai_usage_log", order=""))
    llm._log_usage("claude-opus-4-8",
                   usage=SimpleNamespace(input_tokens=100, output_tokens=50))
    rows = db.query("ai_usage_log", order="")
    assert len(rows) == before + 1
    newest = sorted(rows, key=lambda r: r["timestamp"])[-1]
    assert newest["model"] == "claude-opus-4-8"
    assert newest["token_count_approx"] == 150
    assert newest["module"].startswith("test_disclosure") or newest["module"]


def test_log_usage_approximates_without_usage():
    llm._log_usage("claude-sonnet-4-6", approx_chars=4000)
    newest = sorted(db.query("ai_usage_log", order=""),
                    key=lambda r: r["timestamp"])[-1]
    assert newest["token_count_approx"] == 1000


def test_log_usage_never_raises():
    llm._log_usage(None, usage="not-a-usage-object")  # nonsense input


def test_usage_for_article_links_jobs_and_logs():
    article_id = db.insert("articles", {
        "title": "Disclosure fixture", "article_type": "empirical",
        "content_md": "x", "outline_md": "o",
        "metadata": {"review_md": "memo"},
    })
    job_id = db.insert("jobs", {
        "kind": "article_generate", "ref_table": "articles",
        "ref_id": article_id, "status": "done",
    })
    db.insert("ai_usage_log", {
        "model": "claude-opus-4-8", "module": "nbu_research.modules.writing.pipeline",
        "job_id": job_id, "timestamp": db.now(), "token_count_approx": 1234,
    })
    jobs, logs, date_range = disclosure.usage_for_article(article_id)
    assert len(jobs) == 1 and len(logs) == 1
    assert logs[0]["model"] == "claude-opus-4-8"
    assert date_range[0] is not None and date_range[0] == date_range[1]


def test_generate_disclosure_fails_cleanly_without_key():
    article = db.get("articles", db.insert("articles", {
        "title": "No key", "article_type": "empirical",
    }))
    try:
        disclosure.generate_disclosure(article)
        assert False, "expected RuntimeError without ANTHROPIC_API_KEY"
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


if __name__ == "__main__":
    for name in sorted(k for k in dir() if k.startswith("test_")):
        globals()[name]()
        print(name, "OK")
    print("all disclosure tests passed")
