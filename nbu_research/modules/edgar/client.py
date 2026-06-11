"""Thin SEC EDGAR API client.

SEC EDGAR is free, JSON, and key-less, but it blocks requests without a
descriptive User-Agent and rate-limits at <=10 req/s. This client sets the
required header, sleeps politely between calls, caches the (large) ticker->CIK
map in-process, and raises plain ValueErrors the routes can surface to users.

All endpoints documented at https://www.sec.gov/edgar/sec-api-documentation.
"""
import json
import re
import ssl
import time
import urllib.error
import urllib.request

# A descriptive User-Agent is mandatory; the SEC blocks generic agents.
USER_AGENT = "NBU Research Agent research@nyenrode.nl"

# Python's default SSL store is often empty on macOS/slim containers, which
# makes HTTPS to sec.gov fail with CERTIFICATE_VERIFY_FAILED. Pin certifi's CA
# bundle (a dependency of the stack) so EDGAR works without env-var hacks.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi should always be present
    _SSL_CONTEXT = ssl.create_default_context()

# Polite delay between SEC calls (limit is <=10 req/s; 0.15s keeps us well under).
RATE_LIMIT_SECONDS = 0.15

# Cap filing text fed to the LLM so a 10-K doesn't blow the context window.
MAX_FILING_CHARS = 200_000

_TICKER_MAP = None  # cached {TICKER: {"cik10": str, "title": str}}


def _get_json(url):
    """GET a URL with the required header and return parsed JSON.

    Raises ValueError on HTTP errors (404, blocked, etc.) or bad JSON so the
    caller can show a clear message instead of a raw traceback.
    """
    time.sleep(RATE_LIMIT_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"SEC EDGAR returned 404 (not found) for {url}")
        raise ValueError(f"SEC EDGAR request failed ({e.code}) for {url}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach SEC EDGAR ({e.reason}). Check network access.")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError(f"SEC EDGAR returned non-JSON content for {url}")


def _get_text(url):
    """GET a URL as decoded text (for filing documents)."""
    time.sleep(RATE_LIMIT_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"SEC EDGAR request failed ({e.code}) for {url}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach SEC EDGAR ({e.reason}). Check network access.")
    return raw.decode("utf-8", errors="replace")


def _load_ticker_map():
    """Load and cache the company_tickers map keyed by uppercase ticker."""
    global _TICKER_MAP
    if _TICKER_MAP is not None:
        return _TICKER_MAP
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    mapping = {}
    # The file is a dict of {"0": {cik_str, ticker, title}, ...}.
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        cik10 = str(entry.get("cik_str", "")).zfill(10)
        mapping[ticker] = {"cik10": cik10, "title": entry.get("title", "")}
    _TICKER_MAP = mapping
    return mapping


def ticker_to_cik(ticker):
    """Resolve a ticker symbol to (cik10, company_title).

    Raises ValueError if the ticker is unknown so the caller can skip it.
    """
    ticker = str(ticker).upper().strip()
    if not ticker:
        raise ValueError("Empty ticker")
    mapping = _load_ticker_map()
    entry = mapping.get(ticker)
    if not entry:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR")
    return entry["cik10"], entry["title"]


def submissions(cik10):
    """Return the full submissions document for a zero-padded CIK."""
    return _get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")


def recent_filings(cik10, forms=None, limit=20):
    """Return recent filings as a list of dicts.

    Each item: {form, date, accession, primary_doc, url}. Filter to `forms`
    (a list/set of form types like ["10-K", "10-Q"]) when given.
    """
    data = submissions(cik10)
    recent = (data.get("filings") or {}).get("recent") or {}
    accession_numbers = recent.get("accessionNumber") or []
    form_types = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    primary_docs = recent.get("primaryDocument") or []

    wanted = {f.upper() for f in forms} if forms else None
    cik_int = int(cik10)
    out = []
    for i in range(len(accession_numbers)):
        form = form_types[i] if i < len(form_types) else ""
        if wanted is not None and form.upper() not in wanted:
            continue
        accession = accession_numbers[i]
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        accession_nodashes = accession.replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
            f"{accession_nodashes}/{primary_doc}"
        ) if primary_doc else ""
        out.append({
            "form": form,
            "date": filing_dates[i] if i < len(filing_dates) else "",
            "accession": accession,
            "primary_doc": primary_doc,
            "url": url,
        })
        if len(out) >= limit:
            break
    return out


def company_facts(cik10):
    """Return the XBRL company facts document for a zero-padded CIK."""
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")


def concept_series(cik10, concept, unit="USD", facts=None):
    """Return annual values for one us-gaap concept.

    Pulls the concept from companyfacts, keeps annual (form 10-K) datapoints,
    and dedups by fiscal year (latest reported value per year wins). Returns a
    list of {end, val, fy, fp, form}. An empty list means the concept/unit is
    absent for this company (a normal, non-fatal case).

    Pass a pre-fetched `facts` dict to avoid re-downloading companyfacts when
    pulling several concepts for the same company.
    """
    if facts is None:
        facts = company_facts(cik10)
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    node = us_gaap.get(concept)
    if not node:
        return []
    units = node.get("units") or {}
    points = units.get(unit)
    if not points:
        return []

    by_year = {}
    for p in points:
        if str(p.get("form", "")).upper() != "10-K":
            continue
        fy = p.get("fy")
        if fy is None:
            continue
        item = {
            "end": p.get("end"),
            "val": p.get("val"),
            "fy": fy,
            "fp": p.get("fp"),
            "form": p.get("form"),
        }
        # Keep the datapoint with the latest period end for each fiscal year.
        existing = by_year.get(fy)
        if existing is None or (item["end"] or "") > (existing["end"] or ""):
            by_year[fy] = item
    return [by_year[fy] for fy in sorted(by_year)]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n\s*\n\s*\n+")


def filing_text(url):
    """Fetch a filing's primary document and return plain text.

    Strips HTML tags and collapses whitespace, then truncates to
    MAX_FILING_CHARS so the result is safe to feed an LLM.
    """
    if not url:
        raise ValueError("No filing document URL available")
    html = _get_text(url)
    # Drop script/style blocks wholesale before stripping remaining tags.
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = _TAG_RE.sub(" ", html)
    # Decode the handful of entities that survive tag stripping.
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&#160;", " "), ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "'"),
        ("&ldquo;", '"'), ("&rdquo;", '"'), ("&mdash;", "-"), ("&ndash;", "-"),
    ):
        text = text.replace(entity, char)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    if len(text) > MAX_FILING_CHARS:
        text = text[:MAX_FILING_CHARS] + "\n\n[... truncated for analysis ...]"
    return text
