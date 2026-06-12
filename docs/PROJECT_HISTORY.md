# Project history — NBU Research Agent

Chronological build log for future sessions (human and AI). What was built,
what was decided, what broke, and what is still open. Conventions live in
`CLAUDE.md`; module contracts in `docs/INTERFACES.md`; this file is the story.

## Context

Built by Dieter Vlaminck (Digital Learning & AI Literacy Specialist, Nyenrode)
with Claude (Fable 5 / Opus 4.8) during Anthropic's limited Fable window, as
the most complete academic research agent he could conceive — and as the live
demo for the **June 15, 2026 session on agents and research**. Stakeholders:
Michael Erkens (Rector Magnificus — feature requests), Stephan van Heusden
(IT — Azure hosting), Narges Saffari (ASC — LSEG/Refinitiv licensing).

Repo: https://github.com/dietervlaminck-tech/NBU-research-agent (public, so IT
can review). Predecessors it unifies: NBU-AI-interviewer (became the
interviews module) and academic-research-skills (its skills distilled into
`nbu_research/prompts/`).

## Timeline

### June 10 — v0.1: the platform in one day

- Core architecture: Flask blueprint-per-module, plain sqlite3 + JSON columns
  (`db.py`), all Anthropic calls through `llm.py` (4 call shapes), background
  jobs via `jobs.py`, Nyenrode branding throughout.
- Modules built (5 parallel agents against `docs/INTERFACES.md`): interviews
  (ported from NBU-AI-interviewer incl. SSE chat + share panel), surveys
  (native engine, AI-assisted design, 7 question types), analysis (AI thematic
  coding + 7 statistics with APA-style AI interpretation), literature
  (web-search-grounded pipeline), writing (outline → sections → peer-review
  memo → revision), exports (CSV/XLSX/JSON/SAV/QSF/QDPX/DOCX/MD/HTML/LaTeX/
  BibTeX registry).
- Verified end-to-end live; deployed tooling added later that day
  (`deploy.sh`, parked GitHub Actions workflow, `CLAUDE.md`).
- **Demo produced** for the June 15 session: mock study *"AI adoption in
  university teaching"* — 4 persona-played AI interviews, one-click thematic
  analysis (34 codes / 9 themes / 77 quotes), Nyenrode-styled deck
  (`demo/NBU-research-agent-demo.pptx`) + `demo/PRESENTER_SCRIPT.md` (Dutch,
  3-minute timing, FAQ).
- Git/GitHub: public repo; CI workflow lives at
  `docs/github-actions-deploy.yml` because the PAT lacks `workflow` scope —
  copy to `.github/workflows/deploy.yml` to activate.

### June 11 — stakeholder track (M. Erkens), all phases same day

Requested: Stata/R/Python beyond SPSS; Refinitiv data pull; SEC EDGAR.
Deadline June 22; finished June 11.

- **Phase 1 — stats packages:** study exports to Stata `.dta`, R `.rds`,
  Jupyter notebook (data embedded); per-analysis **replication packages**
  (data + equivalent .do/.R/.py scripts + codebook + README) — OSF/journal
  ready. Gotcha: pyreadr's librdata segfaults next to pyreadstat in-process →
  `.rds` writes run in a subprocess; pyreadr also can't write 0-row frames.
- **Phase 2 — datasets + EDGAR:** `datasets` table (CSV text + typed column
  spec); analysis engine made polymorphic (`responses_dataframe`/
  `dataframe_columns`/`run_analysis` accept study OR dataset — dispatch on the
  presence of `study_type`); upload module (CSV/XLSX/SAV/DTA, type inference,
  `datasets.store.from_dataframe` is the canonical helper connectors must
  use); SEC EDGAR connector (ticker→CIK, XBRL company facts → panel datasets,
  AI 10-K analysis; needs the descriptive User-Agent; certifi-pinned SSL
  context). Verified live: 5-firm × 7-year panel, OLS regression R²=0.81.
- **Phase 3 — Refinitiv:** pluggable connector (desktop + platform session,
  dormant until configured; `lseg-data` lazy-imported). Credentials received
  were a **Workspace desktop seat (eikon2, until Aug 15)** — works on
  Dieter's Mac only. Verified live on the desktop session (Apple revenue
  $416.16B — matches EDGAR exactly). **Azure hosting still needs an RDP
  machine/service account from LSEG** — email drafted to Erkens/Saffari/IT
  with the formal request; see `docs/REFINITIV_DESIGN.md`.

### June 11–12 — v0.1.1: multi-user foundation (five-feature work order)

1. **Entra ID SSO** (`auth.py`, msal auth-code flow, tid validation, global
   guard with public allowlist for respondent routes; dev mode = synthetic
   user when AZURE_* unset; setup steps in `docs/ENTRA_SETUP.md`).
2. **Per-project roles** viewer<collaborator<owner (`project_members`,
   email invites converting on first login, members UI, `check_project_role`
   enforcement across project/analysis/dataset/study routes; legacy projects
   without members are grandfathered).
3. **AI disclosure** — `ai_usage_log` written centrally by `llm.py` for every
   AI call (model, module via stack inspection, job via contextvar, user via
   session, tokens); `/writing/<id>/disclosure` generates APA/Springer/generic
   statements from the recorded history, cached in article metadata.
4. **Durable task queue** — jobs registered via `@jobs.job(kind)` with
   JSON-serializable payloads; Celery+Redis when `CELERY_BROKER_URL` set
   (worker: `celery -A nbu_research.worker worker`), thread fallback in dev;
   idempotent on redelivery (done-check in `jobs.execute`); UI polling
   unchanged.
5. **Methods advisor** — `prompts/methods_advisor.py` + POST
   `/projects/<id>/methods-advisor`; advisory panel intercepts "New Study"
   on the project hub; result stored in `projects.methods_check_json`.
   Live-tested with a deliberately flawed design → 5 errors, correct call.

73 tests passing. Pushed as 5 commits (one per feature) + fixes.

### June 12 — v0.2: methods depth

Four parallel agents + integration pass, same playbook as v0.1. EFA/MANOVA/
nonparametrics/effect-size report (note: factor_analyzer 0.5.1 needs the
sklearn>=1.6 compat shim in quantitative.py), deductive coding + Cohen's κ
(session × code presence) + co-occurrence, multi-page/skip-logic/randomized
surveys + validated scale library (UWES-9, TAM, BFI-10, PSS-4), transcript +
Qualtrics CSV imports, browser voice input + multilingual interviewing, and the
mixed-methods joint display (`analysis/mixed.py`). New analysis kinds dispatch
through `quantitative.ANALYSIS_KINDS` (merged with `qualitative.SYNC_KINDS`);
forms render generically from `quantitative.PARAM_SPECS`. 143 tests. Live-
verified: Kruskal-Wallis + effect-size report on the EDGAR dataset,
co-occurrence on the demo interviews. NOTE: the regenerated demo study id is
6eff78d31eb5 (ids changed when demo data was rebuilt after the data-loss
incident).

### June 12 — v0.3: integrations depth

Per-user connections spine (`credentials.py` + Settings → Connections, keyed
to the Entra identity — Dieter's design) then four parallel agents: Qualtrics
REST (push QSF / pull responses→datasets), Zotero push/pull (Mendeley dormant
— Elsevier OAuth app needed), OpenAlex+Crossref enrichment (verified live:
Hardin 1968, 22,882 citations) + PDF ingestion with grounded re-synthesis,
article PDF export (reportlab; xhtml2pdf rejected — needs system cairo) + OSF
preregistration packages (zip verified live; API push mock-verified).
175 tests. Connector API paths beyond enrichment are mock-verified until real
accounts are connected.

## Incidents & lessons (read these before touching anything)

1. **`rm -rf data` destroyed the live database (June 11).** A compile check
   pointed at the real `data/` dir instead of a temp dir; the June 10 demo
   data was lost (no Time Machine) and regenerated from
   `/tmp/simulate_respondents.py`. RULE (now in CLAUDE.md): never aim
   destructive commands at `data/`; tests always use a temp `NBU_DATA_DIR`.
2. **Structured-outputs schema subset.** The API 400s on
   `additionalProperties` unset, `minItems>1`, `maxItems`,
   `minimum/maximum`, `minLength/maxLength`, `pattern`. Both bugs hit live
   (thematic codebook June 10, article outline June 11). Fixed centrally:
   `llm._strict_schema` normalizes every schema — never hand-fix one module.
3. **SQLite lock leak.** db helpers used to leak the connection (open write
   transaction) when a statement raised — one failed INSERT then locked the
   whole DB. All helpers now rollback+close in `finally`.
4. **pyreadr × pyreadstat symbol clash** segfaults in-process → subprocess
   isolation for `.rds` (see `exports/stats_packages.py`).
5. **`worker.py` load_dotenv side effect**: importing it re-injects real
   `.env` keys into a test process — tests that import it must scrub env after.
6. **GitHub pushes**: assistant pushes to this public repo may be blocked by
   safety tooling; Dieter can always run `git push` himself. PAT lacks
   `workflow` scope (see CI note above).

## Current state (June 12, 2026)

- All modules live-verified except: full Entra OAuth round-trip and Celery
  against real Redis (both need the Azure environment — flagged to IT), and
  the Refinitiv *platform* session (needs LSEG machine account).
- Local `.env` holds the working Anthropic key + LSEG desktop app key
  (gitignored; never committed — verified across history).
- Demo data for June 15 lives in local `data/` (regenerated): project
  *AI adoption in university teaching*, 4 interviews, thematic analysis,
  plus EDGAR/Refinitiv panels and a generated article *"AI in teaching
  (retry)"* (the original errored attempt may still sit in the list).
- Reminders scheduled: June 16 (chase LSEG credentials / Phase 3 follow-up),
  June 19 (final integration + deploy checklist before June 22).

## Open items

1. IT (Stephan): host on Azure — `deploy.sh` is the first-deploy path; add
   AZURE_*/CELERY_* app settings; Azure Cache for Redis + a worker container
   for the durable queue; activate the CI workflow.
2. LSEG: RDP API entitlement + machine account (email sent June 11) →
   then set LSEG_SESSION=platform credentials in Azure.
3. Entra app registration per `docs/ENTRA_SETUP.md`, then real-tenant login
   test.
4. June 15 session: deck + presenter script in `demo/`; live demo data ready.
5. Roadmap v0.2+ (methods depth, Qualtrics API, OpenAlex, PDF ingestion)
   in `docs/ROADMAP.md`.
