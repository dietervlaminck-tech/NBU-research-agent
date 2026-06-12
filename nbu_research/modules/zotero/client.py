"""Thin Zotero Web API v3 client.

Talks to https://api.zotero.org with the researcher's own API key (per-user
credentials, see credentials.py — never a platform-wide key). Covers exactly
what the connector needs: push items in 50-item batches, list/create
collections, and read a collection's items. Raises plain ValueErrors with
user-facing messages (bad key, unknown user id) so the routes can re-render
the page instead of leaking a traceback.

API docs: https://www.zotero.org/support/dev/web_api/v3/basics
"""
import json
import re
import ssl
import urllib.error
import urllib.request
import uuid

API_BASE = "https://api.zotero.org"

# Zotero rejects write batches larger than 50 items.
BATCH_SIZE = 50

# Read paging: Zotero caps `limit` at 100; follow `start` offsets, with a
# sanity cap so a pathological library can't loop forever.
PAGE_SIZE = 100
MAX_PAGES = 20

# Python's default SSL store is often empty on macOS/slim containers. Pin
# certifi's CA bundle (a dependency of the stack) like the EDGAR client does.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi should always be present
    _SSL_CONTEXT = ssl.create_default_context()


def _request(method, path, api_key, body=None, write=False):
    """Issue one API call and return parsed JSON.

    Raises ValueError with a user-facing message on HTTP errors so routes can
    surface it directly (403 = bad/insufficient key, 404 = unknown user or
    collection).
    """
    headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if write:
        headers["Zotero-Write-Token"] = uuid.uuid4().hex
    req = urllib.request.Request(API_BASE + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ValueError(
                "Zotero rejected the API key (403). Check the key at "
                "zotero.org → Settings → Security and make sure it has "
                "library read/write access, then update it under "
                "Settings → Connections.")
        if e.code == 404:
            raise ValueError(
                "Unknown Zotero user id or collection (404). Verify the "
                "numeric user ID shown on zotero.org's API keys page and "
                "update it under Settings → Connections.")
        raise ValueError(f"Zotero API request failed ({e.code}) for {path}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach Zotero ({e.reason}). Check network access.")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Zotero returned non-JSON content for {path}")


# --- author-string parsing ----------------------------------------------------

# "J.", "J. R.", "J.R.", or a bare single capital — initials, not a full name.
_INITIALS_RE = re.compile(r"^(?:[A-Z]\.?[\s-]*)+$")


def _creator_from_name(part):
    """One creator dict from a single person's name string."""
    part = part.strip().strip(",")
    if not part:
        return None
    if "," in part:
        # "Smith, J." / "Smith, Jane" — last name first.
        last, _, first = part.partition(",")
        last, first = last.strip(), first.strip()
        if last and first:
            return {"creatorType": "author", "lastName": last, "firstName": first}
        part = (last or first)
    words = part.split()
    if len(words) >= 2:
        return {"creatorType": "author",
                "firstName": " ".join(words[:-1]), "lastName": words[-1]}
    # Single token (mononym, organisation acronym) — Zotero accepts a bare name.
    return {"creatorType": "author", "name": part}


def parse_authors(s):
    """Tolerantly parse our free-text `authors` column into Zotero creators.

    Handles "Jane Smith, John Doe", "Smith, J., & Doe, J.", semicolon lists,
    "and"/"&" separators, and single names. Never raises — anything
    unparseable falls back to one creator with a bare "name" field.
    """
    s = (s or "").strip()
    if not s:
        return []
    try:
        # Normalise the explicit separators to ';' first: "&", " and ",
        # optionally preceded by a comma (APA's ", & ").
        text = re.sub(r",?\s*(?:&|\band\b)\s+", ";", s)
        if ";" in text:
            parts = [p for p in (x.strip() for x in text.split(";")) if p]
        elif "," in text:
            chunks = [c.strip() for c in text.split(",") if c.strip()]
            if any(_INITIALS_RE.match(c) for c in chunks):
                # "Smith, J., Doe, J." — pair each surname with its initials.
                parts, i = [], 0
                while i < len(chunks):
                    if i + 1 < len(chunks) and _INITIALS_RE.match(chunks[i + 1]) \
                            and not _INITIALS_RE.match(chunks[i]):
                        parts.append(f"{chunks[i]}, {chunks[i + 1]}")
                        i += 2
                    else:
                        parts.append(chunks[i])
                        i += 1
            else:
                # "Jane Smith, John Doe" — plain comma-separated full names.
                parts = chunks
        else:
            parts = [text]
        creators = [c for c in (_creator_from_name(p) for p in parts) if c]
        if creators:
            return creators
    except Exception:
        pass
    return [{"creatorType": "author", "name": s}]


def source_to_item(source, collection_key=None):
    """Build a Zotero journalArticle payload from one of our `sources` rows."""
    item = {
        "itemType": "journalArticle",
        "title": source.get("title") or "(untitled)",
        "creators": parse_authors(source.get("authors") or ""),
        "publicationTitle": source.get("venue") or "",
        "date": str(source.get("year") or ""),
        "DOI": source.get("doi") or "",
        "url": source.get("url") or "",
        "abstractNote": source.get("abstract") or "",
    }
    if collection_key:
        item["collections"] = [collection_key]
    return item


# --- write endpoints ----------------------------------------------------------

def push_items(payloads, api_key, user_id):
    """POST item payloads in batches of 50.

    Returns {"created": [keys], "failed": n, "by_index": {input_index: key}}
    — `by_index` lets the caller map created keys back to the exact payloads
    that produced them even when some items in a batch fail.
    """
    created, by_index, failed = [], {}, 0
    for offset in range(0, len(payloads), BATCH_SIZE):
        batch = payloads[offset:offset + BATCH_SIZE]
        resp = _request("POST", f"/users/{user_id}/items", api_key,
                        body=batch, write=True)
        successful = resp.get("successful") or {}
        for idx, item in successful.items():
            key = (item or {}).get("key", "")
            if key:
                by_index[offset + int(idx)] = key
        failed += len(resp.get("failed") or {})
    created = [by_index[i] for i in sorted(by_index)]
    return {"created": created, "failed": failed, "by_index": by_index}


def list_collections(api_key, user_id):
    """The user's collections as [{"key", "name"}]."""
    out, start = [], 0
    for _ in range(MAX_PAGES):
        page = _request(
            "GET",
            f"/users/{user_id}/collections?format=json&limit={PAGE_SIZE}&start={start}",
            api_key)
        if not isinstance(page, list) or not page:
            break
        for coll in page:
            data = coll.get("data") or {}
            if data.get("key"):
                out.append({"key": data["key"], "name": data.get("name", "")})
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def create_collection(name, api_key, user_id):
    """Create a collection and return its key."""
    resp = _request("POST", f"/users/{user_id}/collections", api_key,
                    body=[{"name": name}], write=True)
    successful = resp.get("successful") or {}
    for item in successful.values():
        key = (item or {}).get("key", "")
        if key:
            return key
    raise ValueError(f"Zotero did not create the collection '{name}'.")


def collection_items(key, api_key, user_id):
    """All non-attachment items in a collection as a list of `data` dicts."""
    out, start = [], 0
    for _ in range(MAX_PAGES):
        page = _request(
            "GET",
            f"/users/{user_id}/collections/{key}/items"
            f"?format=json&itemType=-attachment&limit={PAGE_SIZE}&start={start}",
            api_key)
        if not isinstance(page, list) or not page:
            break
        out.extend((item.get("data") or {}) for item in page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out
