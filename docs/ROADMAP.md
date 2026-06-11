# Roadmap

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
  [REFINITIV_DESIGN.md](REFINITIV_DESIGN.md). Desktop credentials received;
  Azure machine account still pending from LSEG. Live path unverified until a
  Workspace + app key are in place.
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

## v0.4 — collaboration & scale
- [ ] Researcher accounts + per-project access (currently single-tenant by deployment)
- [ ] Postgres + task queue (replace SQLite/threads) if multi-user load requires it
- [ ] Audit trail for AI assistance (which model produced what, for disclosure statements)
- [ ] GDPR tooling: respondent consent records, data retention policies, anonymization pass over transcripts

## Continuous
- Keep model ids current; re-tune prompt packs against new Claude releases
- Grow the prompt packs with Dieter's reviewing/writing conventions
