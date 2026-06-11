# Module Contracts

Every module is a Flask blueprint in `nbu_research/modules/<name>/` (or a single
`<name>.py`). Modules own only their folder and their template subfolder
`nbu_research/templates/<name>/`. Registration lives in `nbu_research/__init__.py`
and is fixed — do not edit shared files.

## Shared infrastructure

| Import | Provides |
|---|---|
| `from .. import db` (or `from ... import db`) | `insert/update/get/query/delete(table, ...)`, `new_id()`, `now()`. JSON columns auto-encode/decode (see `db.JSON_FIELDS`). |
| `from ..jobs import start_job, update_progress` | Background pipelines. `start_job(kind, fn)` runs `fn(job_id)` in a thread; UI polls `GET /api/jobs/<id>` → `{status, progress, message, result}`. |
| `from ..llm import stream_text, complete, complete_json, research` | All Anthropic calls. Never instantiate `anthropic.Anthropic` directly. |
| `from ..config import ...` | `DEFAULT_INTERVIEW_MODEL`, `DEFAULT_PIPELINE_MODEL`, `AVAILABLE_MODELS`. |

## Blueprint registration (fixed)

| Module | Blueprint variable | url_prefix | Blueprint name |
|---|---|---|---|
| projects | `bp` in `modules/projects.py` | `/` | `projects` |
| interviews | `bp` in `modules/interviews/__init__.py` | `/interviews` | `interviews` |
| surveys | `bp` in `modules/surveys/__init__.py` | `/surveys` | `surveys` |
| analysis | `bp` in `modules/analysis/__init__.py` | `/analysis` | `analysis` |
| literature | `bp` in `modules/literature/__init__.py` | `/literature` | `literature` |
| writing | `bp` in `modules/writing/__init__.py` | `/writing` | `writing` |
| exports | `bp` in `modules/exports/__init__.py` | `/exports` | `exports` |

Each blueprint must declare `bp = Blueprint("<name>", __name__)` and an index
route at `/` (so `/interviews/`, `/surveys/`, … resolve).

## Data model (tables in `db.SCHEMA`)

- `projects` — umbrella for studies, reviews, articles, analyses.
- `studies` — one collection instrument; `study_type` ∈ {interview, survey};
  instrument definition lives in the `config` JSON column:
  - interview config: `{"interview_outline": str, "general_instructions": str}`
  - survey config: `{"questions": [Question], "welcome_text": str, "thankyou_text": str}`
- `sessions` — interview conversations; `messages` = `[{"role","content"}]`.
- `survey_responses` — `answers` = `{question_id: value}`.
- `codebooks` — `codes` = `[{"id","name","description","parent_id"}]`.
- `coded_segments` — one coded quote from one session.
- `analyses` — `kind` ∈ {thematic, descriptives, reliability, ttest, anova, correlation, regression, crosstab}; inputs in `params`, outputs in `results`.
- `literature_reviews` + `sources`.
- `articles` — markdown drafts with `metadata` JSON.

### Survey Question object

```json
{
  "id": "q1",
  "type": "likert|multiple_choice|checkbox|open|numeric|matrix|dropdown",
  "text": "…",
  "required": true,
  "options": ["…"],            // choice types
  "scale": {"min":1, "max":5, "min_label":"…", "max_label":"…"},  // likert/numeric
  "rows": ["…"]                 // matrix sub-items, each rated on `scale`
}
```

Answers: likert/numeric → number; multiple_choice/dropdown → option string;
checkbox → list of option strings; open → string; matrix → `{row_text: number}`.

## Datasets (Phase 2 — analyzable tabular data)

A `datasets` row is a first-class analysis target alongside surveys. Columns:
`id, project_id, name, description, source (upload|edgar|refinitiv),
source_meta (JSON), columns (JSON), data_csv (TEXT), n_rows, status, created_at`.

- `data_csv` holds the full table as CSV text (header row = column ids).
- `columns` is `[{"id","label","kind"}]`, `kind ∈ {numeric, categorical}` —
  the **same shape** the analysis module's `dataframe_columns()` returns.
- Column ids must be analysis-safe: lowercase, `[a-z0-9_]`, start with a letter
  (use `analysis.quantitative._slug` conventions). Dedup collisions.

**Analysis & exports already work on datasets** (foundation done):
- `analysis.quantitative.responses_dataframe(target)` and `dataframe_columns(target)`
  accept a study **or** a dataset row (dispatch: a study has `study_type`, a
  dataset doesn't). Don't pass a bare id — pass the row dict.
- `analysis.quantitative.run_analysis(target, kind, params)` stores `dataset_id`
  when the target is a dataset.
- Analysis hub for a dataset: `GET /analysis/dataset/<id>`; run via
  `POST /analysis/dataset/<id>/run`. Link to these from dataset views.
- Replication exports (`/exports/analysis/<analysis_id>`) work for
  dataset-based analyses automatically.

**To create a dataset from a DataFrame**, build the row yourself via `db.insert`:
infer `columns` (numeric vs categorical via pandas dtype), set `data_csv =
df.to_csv(index=False)`, `n_rows = len(df)`, `source`, and `source_meta`. The
datasets module exposes `datasets.store.from_dataframe(project_id, name, df,
source, source_meta, description)` as the canonical helper — connectors (EDGAR,
Refinitiv) must use it so column typing stays consistent.

## Export registry

`modules/exports/__init__.py` exposes `EXPORTERS`, a dict
`{format_key: {"label": str, "applies_to": "study|article|review|analysis", "fn": callable}}`
where `fn(obj_id) -> (bytes, filename, mimetype)`. Other modules link to
`/exports/<applies_to>/<obj_id>` which lists applicable formats.

Sanctioned exception to the no-cross-module-imports rule: `exports` may import
the analysis module's `responses_dataframe`/`dataframe_columns` (one-way) so
replication scripts reference exactly the column ids used in analyses.

## UI conventions

Templates extend `base.html` (blocks: `title`, `body`, `scripts`); reuse classes
from `static/style.css` (`container`, `card`, `btn btn-primary`, `form-group`,
`table`, `badge`). Respondent-facing pages (interview chat, survey runner) use
`{% block header %}{% endblock %}` overrides to hide researcher nav.
