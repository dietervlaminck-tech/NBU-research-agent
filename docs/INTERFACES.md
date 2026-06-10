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

## Export registry

`modules/exports/__init__.py` exposes `EXPORTERS`, a dict
`{format_key: {"label": str, "applies_to": "study|article|review", "fn": callable}}`
where `fn(obj_id) -> (bytes, filename, mimetype)`. Other modules link to
`/exports/<applies_to>/<obj_id>` which lists applicable formats.

## UI conventions

Templates extend `base.html` (blocks: `title`, `body`, `scripts`); reuse classes
from `static/style.css` (`container`, `card`, `btn btn-primary`, `form-group`,
`table`, `badge`). Respondent-facing pages (interview chat, survey runner) use
`{% block header %}{% endblock %}` overrides to hide researcher nav.
