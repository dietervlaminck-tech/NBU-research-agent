# Architecture

## Module map

```
nbu_research/
├── __init__.py          create_app(): registers one blueprint per module
├── config.py            model ids, paths, secrets
├── db.py                SQLite schema + generic insert/update/get/query helpers
├── llm.py               all Anthropic calls: stream_text / complete / complete_json / research
├── jobs.py              background job runner (threads + jobs table, UI polls /api/jobs/<id>)
├── prompts/             distilled prompt packs (ported from academic-research-skills)
├── modules/
│   ├── projects.py      project hub (/)
│   ├── interviews/      AI interviewer (ported from NBU-AI-interviewer)   /interviews
│   ├── surveys/         native survey engine                              /surveys
│   ├── analysis/        thematic coding + statistics                      /analysis
│   ├── literature/      web-grounded literature research pipeline         /literature
│   ├── writing/         article generation + review + revision            /writing
│   └── exports/         format registry + download routes                 /exports
├── templates/<module>/  Jinja templates per module, all extend base.html
└── static/style.css     Nyenrode corporate identity
```

Module boundaries are governed by [INTERFACES.md](INTERFACES.md): modules own
their folder + template subfolder only, and talk to each other through the
shared tables and the export registry — never by importing each other.

## Data model

```
projects ─┬─ studies (interview|survey, instrument in config JSON)
          │    ├─ sessions          (interview conversations)
          │    ├─ survey_responses  (answers JSON per respondent)
          │    ├─ codebooks ── coded_segments   (qual analysis, QDPX-exportable)
          │    └─ analyses          (stats / thematic runs: params + results JSON)
          ├─ literature_reviews ── sources
          └─ articles              (markdown drafts + metadata JSON)
jobs      (background pipeline state, polled by the UI)
```

Everything researcher-defined or AI-produced that has flexible shape lives in
JSON columns; everything queried or joined lives in real columns.

## AI usage (one helper module, four call shapes)

| Helper | Used by | API features |
|---|---|---|
| `stream_text` | interview chat | SSE streaming, Sonnet 4.6 |
| `complete` | interpretations, reports, drafting | adaptive thinking, Opus 4.8 |
| `complete_json` | codebooks, coding, outlines, survey design, source extraction | structured outputs (`output_config.format`) |
| `research` | literature/desk research | `web_search_20260209` server tool, `pause_turn` continuation |

Model policy: respondent-facing latency-sensitive chat defaults to Sonnet 4.6;
research/analysis/writing pipelines default to Opus 4.8. Researchers can
override per study.

## Concurrency model

Single gunicorn worker, many threads. SQLite in WAL mode handles the
read-heavy load; background pipelines run as daemon threads writing progress to
the `jobs` table. This is deliberately the simplest thing that works for a
single-institution deployment — see ROADMAP for the Postgres/queue upgrade path
if usage grows.
