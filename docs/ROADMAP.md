# Roadmap

## v0.1.1 — multi-user foundation (June 11, 2026)

- [x] **Microsoft Entra ID SSO** as the sole researcher login (msal,
  authorization-code flow, tenant validation, app roles); respondent links
  stay public; dev mode without Azure config — see
  [ENTRA_SETUP.md](ENTRA_SETUP.md)
- [x] **Per-project roles** (viewer < collaborator < owner): members UI,
  email invites with pending-invite conversion on first login, enforcement
  across project, analysis, dataset and study routes
- [x] **AI disclosure statements**: platform-wide `ai_usage_log` of every AI
  call; per-article disclosure page with APA / Springer / generic formats
  and copy-to-clipboard
- [x] **Durable task queue**: Celery + Redis with idempotent retry; all 7
  pipelines converted to registered jobs; thread fallback keeps the
  single-process dev flow (`USE_EAGER_TASKS`)
- [x] **Methods advisor**: pre-study methodological peer review (paradigm /
  method / analysis / power checks) on the project hub; advisory only,
  stored on the project

## Stakeholder track (M. Erkens, June 2026 — target: June 22)

- [x] **Phase 1 (June 11):** Stata (.dta), R (.rds), Jupyter notebook exports;
  per-analysis replication packages (data + .do/.R/.py scripts + codebook +
  README) for OSF/journal data policies
- [x] **Phase 2 (June 11, ahead of schedule):** dataset upload module
  (CSV/XLSX/SAV/DTA → typed, analyzable datasets) + SEC EDGAR connector (XBRL
  financial-panel builder by ticker + AI filing analysis). Analysis & all
  exports (incl. replication packages) now run on datasets, not just surveys.
  Verified live: a 5-firm / 7-year EDGAR panel regressed end-to-end.
- [x] **Phase 3 (June 11):** Refinitiv connector built & VERIFIED LIVE on desktop session (pluggable +
  platform session, dormant-until-configured, mirrors EDGAR) — see
  [REFINITIV_DESIGN.md](REFINITIV_DESIGN.md). Desktop session verified live
  (real fundamentals pulled into a dataset). Azure machine-account path still
  pending LSEG credentials.
- [ ] **Phase 4 (June 19–21):** integration testing, deploy, buffer

## v0.1 (this repo, working foundation)
- [x] Unified platform: projects, interviews (ported), native surveys, analysis, literature, writing, exports
- [x] AI thematic coding → REFI-QDA export
- [x] Quantitative suite: descriptives, alpha, t-test, ANOVA, correlation, regression, crosstab
- [x] Web-grounded literature research with source extraction + BibTeX
- [x] Article pipeline: outline → draft → peer-review memo → revision
- [x] Exports: CSV, XLSX, JSON, SAV, QSF, QDPX, DOCX, MD, HTML, LaTeX, BibTeX

## v0.2 — methods depth
- [ ] Mixed-methods designs: link interview themes to survey constructs per project
- [ ] Quant: factor analysis (EFA), MANOVA, nonparametric tests (Mann-Whitney, Kruskal-Wallis, Wilcoxon), effect-size report builder
- [ ] Qual: deductive coding with imported codebooks, second-coder simulation + intercoder agreement (Cohen's κ), code co-occurrence matrix
- [ ] Survey logic: branching/skip logic, randomization, multi-page surveys, validated scale library (e.g. UWES, TAM, Big Five) with citations
- [ ] Interview: voice mode (speech-to-text respondents), multilingual interviewing
- [ ] Upload external data: import Qualtrics CSV, SPSS .sav, interview transcripts (docx/txt) for analysis without collecting via the platform

## v0.3 — integrations depth
- [ ] Qualtrics REST API: push surveys, pull responses live
- [ ] Reference managers: Zotero/Mendeley API sync for sources
- [ ] Scholarly APIs for literature: OpenAlex / Semantic Scholar / Crossref enrichment (DOIs, citation counts, PDFs where open access)
- [ ] PDF ingestion: upload papers, ground the literature review in full texts
- [ ] PDF export of articles (LaTeX toolchain or weasyprint)
- [ ] OSF (Open Science Framework) project export for preregistration packages

## v0.4 — scale & compliance
*(Researcher accounts, per-project access, the AI audit trail, and the durable
task queue originally planned here shipped early in v0.1.1.)*
- [ ] Postgres migration (replace SQLite) if multi-user load requires it —
  the Celery/Redis queue is already in place
- [ ] GDPR tooling: respondent consent records, data retention policies, anonymization pass over transcripts

## Continuous
- Keep model ids current; re-tune prompt packs against new Claude releases
- Grow the prompt packs with Dieter's reviewing/writing conventions
