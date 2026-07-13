# Faculty-tailored roadmap (v0.4 → v0.7)

Written July 11, 2026, after v0.1–v0.3 shipped (all core modules, methods
depth, integrations, simulation). This roadmap maps the platform's next phases
onto **Nyenrode's actual research organization**, so every track has a natural
faculty owner. Execution sessions: read `CLAUDE.md` and `docs/PROJECT_HISTORY.md`
first, then work a phase top-to-bottom; each phase lists concrete,
independently shippable items.

Nyenrode's research structure (per nyenrode.nl, July 2026): five expertise
centers — **Accounting, Auditing & Control** · **Corporate Reporting, Finance
& Tax** · **Entrepreneurship, Governance & Stewardship** · **Marketing &
Supply Chain Management** · **Strategy, Organization & Leadership** — plus the
**ESG Innovation Institute**, the **Nyenrode Corporate Governance Institute**,
and the RSM-Nyenrode Initiative.

**Working model:** recruit one faculty champion per center and build against
their live research question (the Erkens/Corporate-Reporting pattern from the
June stakeholder track). Do not build a track without a champion.

---

## v0.4 — Foundation & hardening (prerequisite for everything below)

Multi-user readiness. Mostly IT-dependent or one-afternoon fixes; nothing
faculty-facing scales until this lands.

- [ ] **Azure deployment** (`deploy.sh` first run; AZURE_*/CELERY_* app
  settings; Azure Cache for Redis + worker container; activate the parked CI
  workflow from `docs/github-actions-deploy.yml`; Entra app registration per
  `docs/ENTRA_SETUP.md`; real-tenant login test)
- [ ] **CSRF protection** on all forms (Flask-WTF or equivalent token check;
  respondent-facing forms included)
- [ ] **Backups**: nightly SQLite snapshot with rotation (we lost `data/`
  once — cheapest insurance in the project); document restore procedure
- [ ] **Encrypt per-user connector credentials at rest** (Fernet keyed off
  `SECRET_KEY`; migrate existing `user_credentials` rows in place)
- [ ] **AI cost dashboard**: aggregate `ai_usage_log` per module/user/week,
  approximate € via model price table, soft budget warning banner (the
  credit-exhaustion incident of July 9 must become a graph, not a surprise)
- [ ] **Dutch UI toggle** (respondent-facing pages first: interview chat,
  survey runner; then researcher nav) — audience is largely Dutch
- [ ] **Human-in-the-loop coding verification**: spot-check screen for
  thematic/deductive analyses (sample n coded segments → accept/correct →
  store researcher-agreement rate on the analysis) — makes AI coding
  defensible in peer review
- [ ] Postgres migration remains conditional on multi-user load (queue
  already in place); GDPR tooling (consent records, retention, transcript
  anonymization) moves up if respondent studies go live institution-wide

## v0.5 — Audit & Reporting track
*Champions: Accounting, Auditing & Control + Corporate Reporting, Finance &
Tax (M. Erkens). Nyenrode's flagship domain; ties into the "AI in de Audit"
teaching line.*

- [ ] **Audit analytics module** (`modules/audit/`): monetary-unit sampling
  (planning + evaluation), attribute sampling, Benford's-law first/second
  digit tests — all running on `datasets` rows like the other analyses;
  workpaper-style Excel export (test objective, method, sample, conclusion)
- [ ] Methods review of the audit module by the audit-analytics colleagues
  before any teaching use (statistical correctness sign-off)
- [ ] **ESEF/XBRL Europe connector** (filings.xbrl.org): the European EDGAR —
  Dutch/EU listed-company filings into datasets; reuse the EDGAR
  connector→dataset pattern verbatim
- [ ] **Batch report-text analysis**: tone, readability, YoY similarity over
  multiple uploaded/fetched annual reports (extends the existing single-filing
  AI analysis to comparable panels)

## v0.6 — Governance & ESG track
*Champions: Corporate Governance Institute + ESG Innovation Institute +
Entrepreneurship, Governance & Stewardship.*

- [ ] **Dutch Corporate Governance Code compliance scan**: upload an annual
  report → per-principle coverage matrix with quoted evidence and gaps
- [ ] **CSRD/ESRS gap analysis**: upload a sustainability report → coverage
  vs. ESRS topical standards, with a materiality-style summary
- [ ] **Board & stewardship interview templates**: pre-built interview
  outlines (board evaluation, stewardship, founder transitions) as one-click
  study starters on the existing interviewer
- [ ] Refinitiv ESG bundle is already wired — surface it in this track's UI

## v0.7 — Behavioral track
*Champions: Strategy, Organization & Leadership + Marketing & Supply Chain.
Also serves Dieter's own PhD (psychological ownership × generative AI).*

- [ ] **Longitudinal survey waves**: re-invite the same respondents across
  waves (respondent tokens), wave linkage in the data matrix, attrition stats
- [ ] **Experimental blocks in the survey engine**: vignette experiments and
  simple conjoint tasks with random assignment (randomization already exists;
  add condition tracking as a variable)
- [ ] **Scale-library expansion**: psychological ownership scale, MLQ-style
  leadership scales, core consumer-behavior scales — with citations, like the
  existing UWES-9/TAM/BFI-10/PSS-4 entries
- [ ] Within/mixed ANOVA + paired designs to match the experimental features

---

## Sequencing & caveats

1. **v0.4 first, always.** It is mostly one-off work and everything after it
   multiplies in value once the platform is hosted, safe, and cost-transparent.
2. v0.5 before v0.6/v0.7: auditing + reporting is where Nyenrode's academic
   weight and the rector's own field sit, and an audit-sampling module is a
   genuinely distinctive capability.
3. The center structure above was verified on nyenrode.nl (July 2026); the
   *individual* research agendas were not — before locking v0.6/v0.7 scope,
   talk to one researcher per center. Update this file with named champions
   as they commit.
4. Every new analysis kind must keep the platform invariants: dispatch via
   `ANALYSIS_KINDS`/`PARAM_SPECS`, results JSON-safe (`_py()`), exports handle
   empty data, tests against a temp `NBU_DATA_DIR` (see `CLAUDE.md`).
