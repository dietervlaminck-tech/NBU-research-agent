# NBU Research Agent — Nyenrode Business Universiteit

A full-cycle academic research platform. One web application that takes a research
project from **literature research** through **data collection** (AI interviews +
surveys) and **analysis** (qualitative + quantitative) to a **publishable article
draft**, with standards-based exports to the academic tool ecosystem
(Atlas.ti, NVivo, MAXQDA, SPSS, Qualtrics, Word, LaTeX).

Successor to and superset of
[NBU-AI-interviewer](https://github.com/dietervlaminck-tech/NBU-AI-interviewer)
(now the interviews module) and
[academic-research-skills](https://github.com/dietervlaminck-tech/academic-research-skills)
(its skills ported into server-side agent pipelines).

## What it does

| Phase | Module | Capability |
|---|---|---|
| Frame | Projects | A project bundles everything around one research question |
| Explore | Literature | Web-search-grounded literature & desk research with extracted sources, synthesis report, BibTeX export |
| Collect (qual) | Interviews | Adaptive AI interviewer; respondents join via shareable link (WhatsApp, Telegram, Teams, Slack, email); transcripts in a dashboard |
| Collect (quant) | Surveys | Native survey engine: AI-assisted questionnaire design, 7 question types, same distribution channels, response dashboard |
| Analyze (qual) | Analysis | AI thematic coding: inductive codebook, coded segments, thematic report — exports as REFI-QDA (.qdpx) for Atlas.ti/NVivo/MAXQDA |
| Analyze (quant) | Analysis | Descriptives, Cronbach's alpha, t-test, ANOVA, correlation, OLS regression, crosstab/chi² with APA-style AI interpretation |
| Write | Writing | Outline → section-by-section article drafting grounded in your sources and results → built-in peer-review memo → revision loop |
| Export | Exports | CSV, XLSX, JSON, SPSS .sav, Qualtrics .qsf, REFI-QDA .qdpx, DOCX, Markdown, HTML, LaTeX, BibTeX |

## Architecture

| Component | Technology |
|---|---|
| Web framework | Flask (Python), blueprint per module |
| AI | Anthropic Claude — Opus 4.8 for pipelines, Sonnet 4.6 for live interviews; web search server tool for literature research; structured outputs for all machine-readable steps |
| Database | SQLite (WAL), plain `sqlite3` |
| Long-running pipelines | In-process background jobs (`jobs` table + polling) |
| Streaming | Server-Sent Events |
| Deployment | Docker / Azure Web App |
| Branding | Nyenrode corporate identity throughout |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module map and data model,
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for the export format specifications,
and [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan.

## Local development

```bash
git clone <this-repo>
cd NBU-research-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # add your ANTHROPIC_API_KEY

python app.py          # http://localhost:5050
```

## Azure deployment

Same pattern as the original AI Interviewer:

```bash
az acr build --registry <registry> --image nbu-research-agent:latest .
az webapp create --resource-group <rg> --plan <plan> --name nbu-research-agent \
  --deployment-container-image-name <registry>.azurecr.io/nbu-research-agent:latest
az webapp config appsettings set --name nbu-research-agent --resource-group <rg> --settings \
  ANTHROPIC_API_KEY="sk-ant-..." SECRET_KEY="<random>" NBU_DATA_DIR=/app/data
az webapp config storage-account add --name nbu-research-agent --resource-group <rg> \
  --custom-id data --storage-type AzureFiles --share-name nbu-research-data \
  --mount-path /app/data --account-name <storage-account>
```

## Research ethics

The platform collects respondent data. Before fielding a study: obtain ethics
approval where required, include informed consent in the interview outline /
survey welcome text, and treat exported data per your institution's data
management policy. AI-generated analyses and drafts are research *assistance* —
the researcher remains responsible for verification, interpretation, and
authorship disclosure per journal policy.
