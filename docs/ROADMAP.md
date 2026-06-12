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

## v0.2 — methods depth (June 12, 2026)
- [x] Mixed-methods designs: joint display linking interview themes to survey
  constructs + AI meta-inference report, launched from the project hub
- [x] Quant: EFA (KMO, Bartlett, varimax loadings), MANOVA, Mann-Whitney,
  Kruskal-Wallis, Wilcoxon, and an effect-size report builder with verbal
  magnitude labels
- [x] Qual: deductive coding with imported codebooks (JSON/CSV), second-coder
  simulation + Cohen's κ (session × code presence unitization), code
  co-occurrence matrix
- [x] Survey logic: multi-page, show_if skip logic, option/question
  randomization, validated scale library (UWES-9, TAM, BFI-10, PSS-4) with
  citations surfaced on the dashboard
- [x] Interview: respondent voice input (browser Web Speech API — Chrome/Edge/
  Safari; no server audio pipeline) + multilingual interviewing (config.language)
- [x] Upload external data: interview transcripts (.docx/.txt → sessions),
  Qualtrics 3-header CSV auto-detection; SPSS .sav/Stata .dta upload shipped in
  the Phase 2 datasets module

## v0.3 — integrations depth (June 12, 2026)

All external accounts are **per-user**: researchers connect their own
Zotero/Qualtrics/OSF keys at Settings → Connections after Entra login.

- [x] Qualtrics REST API: push surveys (QSF import), pull responses back as
  analyzable datasets — verified against mocked API responses; first real
  push/pull needs a connected Qualtrics account
- [x] Zotero: push review sources to a collection, import collections as
  sources (DOI/title dedupe) — mock-verified; Mendeley remains a dormant slot
  (its API needs an Elsevier-approved OAuth app)
- [x] OpenAlex + Crossref enrichment of sources (DOI, year, venue, citation
  counts, OA links) — **verified live** against the real APIs
- [x] PDF ingestion: upload papers → extracted full text grounds a
  re-synthesized review
- [x] PDF export of articles (reportlab — xhtml2pdf rejected: needs system
  cairo, unavailable on the slim Azure image; latin-1 glyph normalization)
- [x] OSF: preregistration package (.zip — verified live) + push to an OSF
  project via personal token (mock-verified)

## v0.4 — scale & compliance
*(Researcher accounts, per-project access, the AI audit trail, and the durable
task queue originally planned here shipped early in v0.1.1.)*
- [ ] Postgres migration (replace SQLite) if multi-user load requires it —
  the Celery/Redis queue is already in place
- [ ] GDPR tooling: respondent consent records, data retention policies, anonymization pass over transcripts

## Continuous
- Keep model ids current; re-tune prompt packs against new Claude releases
- Grow the prompt packs with Dieter's reviewing/writing conventions
