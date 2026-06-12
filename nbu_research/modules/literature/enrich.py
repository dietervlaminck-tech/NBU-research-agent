"""Scholarly enrichment via Crossref and OpenAlex.

Both APIs are free, JSON, and key-less; both ask polite users to identify
themselves (a `mailto` query parameter + descriptive User-Agent) in exchange
for the faster "polite pool". Same thin-HTTP-client style as the EDGAR module:
certifi-pinned SSL, a short sleep between calls, ValueErrors with readable
messages.

Match policy for title searches: a candidate is accepted only when its
normalized title (lowercased, non-alphanumerics stripped) equals ours or one
contains the other — otherwise the source is marked "no confident match".
Bibliographic columns (doi/year/venue) are filled ONLY when blank; citation
counts and OA links always go into the source's `meta` JSON.
"""
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from ... import db
from ...jobs import job, start_job, update_progress

MAILTO = "research@nyenrode.nl"
USER_AGENT = f"NBU Research Agent (mailto:{MAILTO})"

# Polite delay between API calls — keeps us well inside both rate limits.
RATE_LIMIT_SECONDS = 0.15

# Python's default SSL store is often empty on macOS/slim containers; pin
# certifi's CA bundle so HTTPS works without env-var hacks (same as EDGAR).
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi should always be present
    _SSL_CONTEXT = ssl.create_default_context()


def _get_json(url):
    """GET a URL politely and return parsed JSON; None on 404 (not found)."""
    time.sleep(RATE_LIMIT_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ValueError(f"Enrichment API request failed ({e.code}) for {url}")
    except urllib.error.URLError as e:
        host = urllib.parse.urlsplit(url).netloc
        raise ValueError(f"Could not reach {host} ({e.reason}). Check network access.")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Enrichment API returned non-JSON content for {url}")


# --- matching ----------------------------------------------------------------

_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _norm_title(title):
    return _NONALNUM_RE.sub("", (title or "").lower())


def titles_match(ours, candidate):
    """Accept a title-search candidate only on normalized equality/containment."""
    a, b = _norm_title(ours), _norm_title(candidate)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _clean_doi(doi):
    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    return doi.strip("/")


# --- Crossref -----------------------------------------------------------------

def _crossref_fields(msg):
    date_parts = (msg.get("issued") or {}).get("date-parts") or []
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    authors = []
    for a in msg.get("author") or []:
        name = f"{a.get('family') or ''}, {a.get('given') or ''}".strip(", ").strip()
        name = name or (a.get("name") or "")
        if name:
            authors.append(name)
    container = msg.get("container-title") or []
    titles = msg.get("title") or []
    return {
        "doi": _clean_doi(msg.get("DOI") or ""),
        "title": titles[0] if titles else "",
        "venue": container[0] if container else "",
        "year": str(year) if year else "",
        "is_referenced_by_count": msg.get("is-referenced-by-count"),
        "authors": authors,
    }


def crossref_lookup(doi="", title=""):
    """Look a work up in Crossref; returns a fields dict or None (no match).

    With a DOI the record is trusted; with only a title the top search hit is
    accepted only when `titles_match` passes.
    """
    if doi:
        data = _get_json(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={MAILTO}"
        )
        if not data:
            return None
        return _crossref_fields(data.get("message") or {})
    if title:
        q = urllib.parse.quote_plus(title)
        data = _get_json(
            f"https://api.crossref.org/works?query.bibliographic={q}&rows=1&mailto={MAILTO}"
        )
        items = ((data or {}).get("message") or {}).get("items") or []
        if not items:
            return None
        fields = _crossref_fields(items[0])
        if not titles_match(title, fields.get("title")):
            return None
        return fields
    return None


# --- OpenAlex -----------------------------------------------------------------

def _openalex_fields(work):
    source = ((work.get("primary_location") or {}).get("source")
              or work.get("host_venue") or {})
    oa_url = ((work.get("open_access") or {}).get("oa_url")
              or (work.get("best_oa_location") or {}).get("pdf_url") or "")
    return {
        "doi": _clean_doi(work.get("doi") or ""),
        "title": work.get("display_name") or work.get("title") or "",
        "venue": (source or {}).get("display_name") or "",
        "year": str(work.get("publication_year") or ""),
        "cited_by_count": work.get("cited_by_count"),
        "oa_pdf_url": oa_url,
    }


def openalex_lookup(doi="", title=""):
    """Look a work up in OpenAlex; returns a fields dict or None (no match)."""
    if doi:
        data = _get_json(
            f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}?mailto={MAILTO}"
        )
        if not data:
            return None
        return _openalex_fields(data)
    if title:
        q = urllib.parse.quote_plus(title)
        data = _get_json(
            f"https://api.openalex.org/works?search={q}&per-page=1&mailto={MAILTO}"
        )
        results = (data or {}).get("results") or []
        if not results:
            return None
        fields = _openalex_fields(results[0])
        if not titles_match(title, fields.get("title")):
            return None
        return fields
    return None


# --- enrichment ---------------------------------------------------------------

def enrich_source(source):
    """Enrich one source row from Crossref + OpenAlex; returns db update dict.

    Policy: never overwrite a non-empty existing column — doi/year/venue are
    filled only when blank (Crossref wins over OpenAlex when both have a
    value). Citation counts and the OA link always land in `meta`. API/network
    failures degrade to "no data from that API" instead of failing the job.
    """
    doi = _clean_doi(source.get("doi") or "")
    title = (source.get("title") or "").strip()

    def _safe(lookup, **kwargs):
        try:
            return lookup(**kwargs)
        except ValueError:
            return None

    crossref = _safe(crossref_lookup, doi=doi, title="" if doi else title)
    oa_doi = doi or (crossref or {}).get("doi") or ""
    openalex = _safe(openalex_lookup, doi=oa_doi, title="" if oa_doi else title)

    updates = {}

    def fill_blank(field, value):
        if not value or not isinstance(value, str):
            return
        if (source.get(field) or "").strip():
            return  # never overwrite a non-empty existing value
        if field not in updates:  # first writer (Crossref) wins
            updates[field] = value.strip()

    meta = dict(source.get("meta") or {})
    if crossref:
        meta["crossref"] = crossref
        fill_blank("doi", crossref.get("doi"))
        fill_blank("year", crossref.get("year"))
        fill_blank("venue", crossref.get("venue"))
    if openalex:
        meta["openalex"] = {
            "cited_by_count": openalex.get("cited_by_count"),
            "oa_pdf_url": openalex.get("oa_pdf_url"),
            "doi": openalex.get("doi"),
        }
        fill_blank("doi", openalex.get("doi"))
        fill_blank("year", openalex.get("year"))
        fill_blank("venue", openalex.get("venue"))
    meta["no_match"] = not (crossref or openalex)
    meta["enriched_at"] = db.now()
    updates["meta"] = meta
    return updates


@job("enrich_sources")
def _enrich_sources_job(job_id, review_id=None):
    sources = db.query("sources", "review_id = ?", (review_id,), order="created_at ASC")
    n = len(sources)
    enriched = no_match = 0
    for i, source in enumerate(sources):
        update_progress(
            job_id,
            i / n if n else 1.0,
            f"Enriching source {i + 1}/{n}: {(source.get('title') or '')[:70]}",
        )
        updates = enrich_source(source)
        if (updates.get("meta") or {}).get("no_match"):
            no_match += 1
        else:
            enriched += 1
        db.update("sources", source["id"], updates)
    return {"enriched": enriched, "no_match": no_match, "review_id": review_id}


def start_enrich_job(review_id):
    return start_job(
        "enrich_sources", {"review_id": review_id},
        ref_table="literature_reviews", ref_id=review_id,
    )
