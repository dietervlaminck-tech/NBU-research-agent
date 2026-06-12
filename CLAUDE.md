# CLAUDE.md

Guidance for Claude Code when working in this repository.

**New session? Read `docs/PROJECT_HISTORY.md` first** — the chronological
build log: what exists, what broke before (and the rules that came from it),
and what is still open.

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

python -m pytest tests/ -q    # 143 tests; no API key needed
./deploy.sh                   # Azure deploy (needs .deploy.env + az login)

# Durable task queue (production; dev falls back to threads without it):
celery -A nbu_research.worker worker --loglevel=info --concurrency=2
```

**Environment variables** (all optional in dev; see `.env.example`):
`ANTHROPIC_API_KEY`, `SECRET_KEY`, `NBU_DATA_DIR`; Entra SSO: `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_REDIRECT_URI` (unset = auth
disabled, synthetic dev user); Refinitiv: `LSEG_SESSION`, `LSEG_APP_KEY`,
`LSEG_CLIENT_ID`, `LSEG_CLIENT_SECRET`; task queue: `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, `USE_EAGER_TASKS`.

**⚠️ NEVER point destructive commands at `data/`** (`rm -rf data`, DROP TABLE,
etc.) — it is the live researcher database. Tests and migration checks must
always run against a temp dir via `NBU_DATA_DIR` (every test file does this
before importing `nbu_research`). This rule exists because a careless
`rm -rf data` in a compile check once destroyed real demo data.

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
| Jobs | `jobs.py` | Pipelines register with `@jobs.job(kind)` and run via `start_job(kind, payload_dict)` — Celery+Redis when `CELERY_BROKER_URL` is set, in-process thread otherwise. Payloads must be JSON-serializable. The UI polls `GET /api/jobs/<id>`. Synchronous stats run inline. |
| Config | `config.py` | Model IDs + paths. |

Modules: `projects` (`/`), `interviews` (`/interviews`), `surveys`
(`/surveys`), `datasets` (`/datasets`), `edgar` (`/edgar`), `refinitiv`
(`/refinitiv`), `analysis` (`/analysis`), `literature` (`/literature`),
`writing` (`/writing`), `exports` (`/exports`).

v0.2 additions: advanced quant kinds register in `quantitative.ANALYSIS_KINDS`
with form specs in `quantitative.PARAM_SPECS` (the hub renders forms
generically); qual sync kinds in `qualitative.SYNC_KINDS`; mixed-methods in
`analysis/mixed.py`; survey skip-logic is evaluated server-side in
`surveys/builder.py.validate_answers` (never trust the client); the scale
library lives in `surveys/scales.py`; Qualtrics CSV detection in
`datasets/qualtrics.py`; transcript import in `interviews/transcripts.py`.

Cross-cutting (v0.1.1): `auth.py` (Entra ID SSO + per-project roles —
`current_user`, `check_project_role`, public-endpoint allowlist),
`worker.py` (Celery app; jobs registered via `@jobs.job(kind)` with
JSON-serializable payloads), `ai_usage_log` (written centrally by `llm.py`,
feeds the per-article AI disclosure), `prompts/methods_advisor.py`
(pre-study design review on the project hub).

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

Single gunicorn web worker, many threads (`startup.sh`); SQLite in WAL mode.
Background jobs: Celery + Redis in production (separate worker container,
`celery -A nbu_research.worker worker`) so web restarts don't kill pipelines;
in-process daemon threads in dev. **Do not scale the web app to multiple
instances** — that splits the SQLite DB. Long pipelines stay under Azure's
~230s request limit because they run as background jobs and the browser polls;
only interview SSE holds a live connection. The Postgres migration path is in
`docs/ROADMAP.md` v0.4.

## When extending

New collection type, analysis, export format, or pipeline → follow the matching
existing module as a template and the contract in `docs/INTERFACES.md`. Add
tests next to `tests/test_exports.py` / `tests/test_quantitative.py` (they set
`NBU_DATA_DIR` to a temp dir before importing `nbu_research`, so they need no
API key for anything but the LLM-interpretation path, which must stay
None-safe).
