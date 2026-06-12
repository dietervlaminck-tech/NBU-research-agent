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
  "rows": ["…"],                // matrix sub-items, each rated on `scale`

  "page": 1,                    // v0.2 optional: 1-based page number (default 1)
  "show_if": {                  // v0.2 optional: skip logic; question is shown
    "question": "q0",           //   only when the referenced EARLIER answer
    "op": "equals|not_equals|in|gte|lte",
    "value": "…"                //   matches (value may be a list for "in")
  },
  "randomize_options": false,   // v0.2 optional: shuffle options per respondent
  "construct": "uwes9"          // v0.2 optional: scale-library construct tag
}
```

Survey config additions (v0.2): `"randomize_questions": bool` (shuffle question
order within each page per respondent; questions with `show_if` keep relative
order after their referenced question).

Skip-logic rules: `show_if` may only reference a question on an EARLIER page or
earlier in the same page; hidden questions are not required and are stored as
missing. `validate_answers` MUST evaluate `show_if` server-side (never trust
the client). The analysis dataframe treats logic-hidden answers as NaN/None —
no schema change.

Validated scale library: `modules/surveys/scales.py` exposes
`SCALE_LIBRARY = {key: {"name", "citation", "scale", "items": [...]}}` (UWES-9,
TAM, BFI-10, …). The builder inserts a scale as likert questions tagged with
`construct`; the citation is stored in the survey config under
`"scale_citations"` so methods sections can cite instruments.

Answers: likert/numeric → number; multiple_choice/dropdown → option string;
checkbox → list of option strings; open → string; matrix → `{row_text: number}`.

### Interview study config additions (v0.2)

`{"language": "en|nl|de|fr|es|…"}` — the interviewer conducts the conversation
in this language (bot system prompt). The respondent chat offers browser
speech-to-text (Web Speech API) using the matching locale; voice is optional
and falls back silently to typing where unsupported.
`{"imported": true}` marks studies created by transcript upload (no live link).

### Analysis kinds (v0.2 additions)

Quantitative (in `analysis/quantitative.py`, same `fn(target, params)` shape):
`efa` (exploratory factor analysis via factor_analyzer), `manova`
(statsmodels), `mannwhitney`, `kruskal`, `wilcoxon` (scipy), and
`effect_sizes` (report builder aggregating effect sizes + CIs from the
study/dataset's completed analyses).

Qualitative (in `analysis/qualitative.py`): `deductive` (coding with a fixed
imported codebook — no inductive step), `intercoder` (independent second-coder
simulation; Cohen's κ computed on the session × code presence matrix — note
this unitization in the report), `cooccurrence` (code co-occurrence counts
within sessions → matrix).

Mixed (in `analysis/mixed.py`): `mixed_methods` — joint display linking a
thematic analysis' themes to a survey/dataset's constructs, with an AI
meta-inference report. Stored with `project_id`.

### Imports (v0.2)

- Interview transcripts: upload .docx/.txt in the interviews module → one
  session per file; lines prefixed `Interviewer:` / `Respondent:` (or
  `I:`/`R:`) become turns, otherwise the whole text is one respondent turn.
- Qualtrics CSV: the datasets module detects Qualtrics' 3-header-row legacy
  export (ids / question text / ImportId JSON) and uses row 1 as ids, row 2 as
  labels, skipping row 3.

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

## Per-user service credentials (v0.3)

Researchers connect their own external accounts (Zotero, Qualtrics, OSF) at
`/settings/connections` after Entra login. Connectors NEVER take platform-wide
keys for these services.

```python
from ...credentials import get_credential   # from a module package
payload = get_credential("zotero")          # dict or None (current user)
```

- `get_credential(service)` returns the payload dict for the **current
  request's user** (works in dev mode via the synthetic dev user) or `None`.
- When `None`, the connector UI must show a "not connected" card linking to
  `/settings/connections` — never error. Same dormant pattern as Refinitiv.
- Payload fields per service are defined in `credentials.SERVICES`
  (zotero: api_key, user_id; qualtrics: api_token, datacenter; osf: token).
- Background jobs do not have a request context: routes must resolve the
  payload BEFORE enqueueing and pass needed values in the job payload
  (job payloads are JSON; treat them as visible in the jobs table — pass
  tokens, fine for this trust boundary, but never log them).

`sources` gained two columns (v0.3): `fulltext` (extracted PDF text) and
`meta` (JSON — enrichment data: DOIs, citation counts, OA links, zotero keys).

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
