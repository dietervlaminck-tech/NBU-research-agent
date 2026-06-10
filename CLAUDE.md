# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**NBU Research Agent** — a Flask web platform that takes an academic research
project from literature research through data collection (AI interviews +
surveys) and analysis (qualitative + quantitative) to a publishable article
draft, with standards-based exports (Atlas.ti/NVivo/MAXQDA, SPSS, Qualtrics,
Word, LaTeX). It unifies two earlier repos:
[NBU-AI-interviewer](https://github.com/dietervlaminck-tech/NBU-AI-interviewer)
(now the interviews module) and
[academic-research-skills](https://github.com/dietervlaminck-tech/academic-research-skills)
(its skills ported into server-side agent pipelines under `nbu_research/prompts/`).

## Run, test, deploy

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set ANTHROPIC_API_KEY and SECRET_KEY
python app.py                 # http://localhost:5050

python -m pytest tests/ -q    # 32 tests; no API key needed
./deploy.sh                   # Azure deploy (needs .deploy.env + az login)
```

The deps that matter beyond Flask/anthropic: `pandas`, `scipy`, `statsmodels`
(quant analysis), `pyreadstat` (SPSS .sav), `python-docx`, `openpyxl`,
`markdown`. Pure-stdlib `zipfile`/`xml.etree` produce .qdpx and .qsf.

## Architecture — read `docs/INTERFACES.md` before adding to a module

One Flask blueprint per module under `nbu_research/modules/`, registered in
`nbu_research/__init__.py` (`create_app`). **Modules own only their own folder
and their `templates/<module>/` subfolder; they never import each other.** They
communicate through the shared SQLite tables and the export registry.

| Layer | File | Rule |
|---|---|---|
| Data | `db.py` | Plain `sqlite3` + generic `insert/update/get/query/delete(table, …)`. JSON columns auto-encode/decode via `db.JSON_FIELDS`. Flexible shapes go in JSON columns; queried/joined fields are real columns. |
| AI | `llm.py` | **Every** Anthropic call goes through `stream_text` / `complete` / `complete_json` / `research`. Never instantiate `anthropic.Anthropic` in a module. |
| Jobs | `jobs.py` | Pipelines that take minutes run via `start_job(kind, fn)` (daemon thread); the UI polls `GET /api/jobs/<id>`. Synchronous stats run inline. |
| Config | `config.py` | Model IDs + paths. |

Modules: `projects` (`/`), `interviews` (`/interviews`), `surveys`
(`/surveys`), `analysis` (`/analysis`), `literature` (`/literature`),
`writing` (`/writing`), `exports` (`/exports`).

## Conventions that bite if missed

- **Model IDs** live in `config.py` and are exact strings — `claude-opus-4-8`,
  `claude-sonnet-4-6`, `claude-haiku-4-5`. Never append date suffixes. Live
  interview chat defaults to Sonnet (latency); pipelines default to Opus.
- **Thinking/structured output**: these models use `thinking={"type":"adaptive"}`
  (no `budget_tokens`) and `output_config={"format": …}` (not `output_format`).
  `llm.py` already does this — copy its call shapes, don't hand-roll.
- **`survey_responses` has no `created_at` column** — pass
  `order="started_at …"` to `db.query` for it, or the default ORDER BY crashes.
- **Study instruments live in `studies.config` (JSON)**, not flat columns:
  interviews → `{interview_outline, general_instructions}`; surveys →
  `{questions:[…], welcome_text, thankyou_text}`. See the Question spec in
  `docs/INTERFACES.md`.
- **Exports** are a flat registry `EXPORTERS` in `modules/exports/__init__.py`;
  keys are prefixed (`study_csv`, `article_docx`, `review_bibtex`). Each
  `fn(obj_id) -> (bytes, filename, mimetype)` must handle empty data without
  crashing.
- **Templates** extend `base.html`; respondent-facing pages (interview chat,
  survey runner) override `{% block header %}{% endblock %}` to hide nav.
  Reuse classes from `static/style.css` (Nyenrode identity) — don't add a CSS
  framework.

## Deployment shape (don't break these invariants)

Single gunicorn worker, many threads (`startup.sh`); SQLite in WAL mode;
background jobs are in-process daemon threads. This is deliberate for a
single-institution deployment. **Do not scale to multiple instances** — that
splits the SQLite DB and orphans jobs. Long pipelines stay under Azure's ~230s
request limit because they run as background jobs and the browser polls; only
interview SSE holds a live connection. The Postgres + queue migration path is
in `docs/ROADMAP.md` v0.4.

## When extending

New collection type, analysis, export format, or pipeline → follow the matching
existing module as a template and the contract in `docs/INTERFACES.md`. Add
tests next to `tests/test_exports.py` / `tests/test_quantitative.py` (they set
`NBU_DATA_DIR` to a temp dir before importing `nbu_research`, so they need no
API key for anything but the LLM-interpretation path, which must stay
None-safe).
