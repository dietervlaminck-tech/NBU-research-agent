# Refinitiv (LSEG) Connector — Design & Status

Requested by M. Erkens (11 June 2026): automatic data pull from LSEG Refinitiv,
for which Nyenrode holds a license. Same pattern extends to other databases;
SEC EDGAR (free) shipped first in Phase 2 and proves the architecture.

## Status (11 June 2026)

**Connector is BUILT, LIVE, and VERIFIED on the desktop session** (11 June
2026). `modules/refinitiv/` mirrors the EDGAR connector→dataset pattern,
supports both session modes, and reads all credentials from environment config
(never committed). 55 tests pass.

**Desktop path verified end-to-end:** with `lseg-data` installed, a desktop app
key in `.env`, and Refinitiv Workspace running, a 3-instrument fundamentals
panel (AAPL.O / MSFT.O / ASML.AS) was pulled through the web UI into an
analysis-ready dataset (Apple revenue $416.16B, matching SEC EDGAR). Refinitiv
field names are auto-sanitized to analysis-safe column ids by the datasets
store. **The platform (machine-account) path remains untested** — pending LSEG
credentials.

**Credentials received (from N. Saffari, ASC, 11 June):** a Refinitiv
**Workspace desktop seat** — `eikon2@nyenrode.nl` + password, blocked until
15 Aug 2026. This is a **named-user desktop account**, which enables the
**desktop session** mode (runs where Workspace is installed, e.g. Dieter's Mac)
— *not* the **platform/machine-account** needed for the Azure-hosted
deployment.

**To activate desktop mode:** install Refinitiv Workspace, sign in as eikon2,
generate a **desktop app key** (App Key Generator inside Workspace, or the
App Key Generator at apidocs.refinitiv.com once signed in), then set
`LSEG_SESSION=desktop` and `LSEG_APP_KEY=...` in `.env` and `pip install
lseg-data`.

**Still needed for Azure hosting:** a Data Platform API **app key + machine
account** (`LSEG_CLIENT_ID`/`LSEG_CLIENT_SECRET`). Academic Workspace licenses
often don't include this; confirm with the LSEG account admin (Narges Saffari)
whether the entitlement is available.

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
