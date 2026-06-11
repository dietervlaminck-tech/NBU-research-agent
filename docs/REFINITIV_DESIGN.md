# Refinitiv (LSEG) Connector — Design

Requested by M. Erkens (11 June 2026): automatic data pull from LSEG Refinitiv,
for which Nyenrode holds a license. Same pattern extends to other databases;
SEC EDGAR (free) ships first in Phase 2 and proves the architecture.

## Architecture: pluggable data sources

New module `modules/datasets/` introduces two concepts:

- **Dataset** (new table `datasets`): a named, versioned tabular artifact inside
  a project — uploaded by the researcher or produced by a connector. All
  quantitative analyses and the Stata/R/Python exports gain `dataset_id` as an
  alternative target to `study_id`.
- **Connector**: a class with `search(query) -> candidates` and
  `fetch(spec) -> DataFrame` plus a config declaration (which credentials it
  needs). Runs as a background job (`jobs.start_job`), writes a dataset row.

```
modules/datasets/
├── __init__.py        blueprint: upload, list, preview, fetch-forms
├── connectors/
│   ├── edgar.py       SEC EDGAR (free, Phase 2)
│   └── refinitiv.py   LSEG Refinitiv (Phase 3, this design)
```

## Refinitiv specifics

**Client library:** `lseg-data` (the current LSEG Data Library for Python,
successor of `refinitiv-data`/Eikon API). Two session types:

| Session | Needs | Fits us? |
|---|---|---|
| Desktop session | LSEG Workspace running on the same machine | Dev/test only |
| Platform session | `app_key` + machine account (client id/secret) from the LSEG admin | **Yes — server deployments** |

**Configuration** (Azure app settings / `.env`; never in git):

```
LSEG_APP_KEY=...
LSEG_CLIENT_ID=...        # machine account
LSEG_CLIENT_SECRET=...
```

The connector registers as "available" only when these are set; otherwise the
UI shows a "not configured" card with a pointer to this document.

**Initial capability set** (matches `lseg-data`'s stable surface):

1. `get_history(universe, fields, start, end)` — daily prices / fundamentals
   time series for a ticker/RIC list → long-format dataset.
2. `get_data(universe, fields)` — point-in-time fundamentals & reference data
   (TR.* fields, e.g. `TR.Revenue`, `TR.ESGScore`) → wide dataset.
3. Researcher UI: paste a ticker/RIC list (or upload CSV column), pick a field
   bundle (prices / fundamentals / ESG), date range → background job → dataset
   → analyze in-platform or export to Stata/R/SPSS.

**Open questions for LSEG admin / IT (drafted in the stakeholder email):**

1. Does the Nyenrode license include the **Data Platform APIs** (machine
   account), or Workspace desktop only? (Determines session type.)
2. Who is the LSEG account admin who can create an app key + machine account?
3. Which content sets are entitled (pricing, fundamentals, ESG, estimates)?
   Entitlements decide which field bundles we expose.
4. Are there license restrictions on storing/redistributing pulled data
   (datasets persist in the platform's database and exports)? Typical academic
   licenses allow internal research use; needs confirmation in writing.
5. Rate/volume limits per month — the connector should display remaining quota
   if the API exposes it.

**Effort estimate once credentials exist:** the connector itself is ~1 day
(the dataset plumbing arrives with Phase 2); entitlement debugging typically
adds another.

## Why EDGAR first

SEC EDGAR needs no license: JSON APIs for filing indexes, full-text search,
and XBRL company facts (only a User-Agent header and ≤10 req/s). It exercises
the exact same connector + dataset + background-job path, so when LSEG
credentials arrive, Refinitiv drops into a proven socket.
