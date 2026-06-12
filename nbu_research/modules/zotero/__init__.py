"""Zotero connector.

Bridges literature reviews and the researcher's own Zotero library, both ways:

- PUSH — a review's sources become items in a Zotero collection named after
  the review (the collection key is remembered in the review's `scope` JSON
  under "zotero_collection"; each source remembers its Zotero item key in
  `meta.zotero_key`, so re-pushing only sends new sources).
- PULL — a Zotero collection's items become sources on an existing review or
  a fresh "imported" review, deduplicated by DOI (or case-insensitive title).

Per-user credentials only (credentials.get_credential("zotero") →
{"api_key", "user_id"}); when not connected the page shows a card linking to
/settings/connections and never errors. Pushes run synchronously — Zotero
accepts 50-item batches and typical reviews hold a few dozen sources.
"""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from ... import db
from ...credentials import get_credential
from . import client

bp = Blueprint("zotero", __name__)

# Zotero shows collection names in a narrow sidebar; keep them readable.
MAX_COLLECTION_NAME = 80


def _review_label(review):
    """A human-readable short label for a review (they have no title column)."""
    label = (review.get("research_question") or "").strip() or "Literature review"
    if len(label) > MAX_COLLECTION_NAME:
        label = label[:MAX_COLLECTION_NAME - 1].rstrip() + "…"
    return label


def _creators_to_string(creators):
    """Zotero creators back to our single authors string ("Jane Smith, John Doe")."""
    names = []
    for c in creators or []:
        name = c.get("name") or " ".join(
            p for p in (c.get("firstName"), c.get("lastName")) if p)
        if name:
            names.append(name)
    return ", ".join(names)


def _context(**extra):
    """Everything index.html needs; extras carry an error or a result summary."""
    cred = get_credential("zotero")
    ctx = {
        "connected": bool(cred),
        "error": None, "push_result": None, "pull_result": None,
        "reviews": [], "collections": [], "projects": [],
    }
    if cred:
        reviews = db.query("literature_reviews")
        for r in reviews:
            r["label"] = _review_label(r)
            r["source_count"] = len(db.query(
                "sources", "review_id = ?", (r["id"],), order=""))
        ctx["reviews"] = reviews
        ctx["projects"] = db.query("projects")
        try:
            ctx["collections"] = client.list_collections(
                cred["api_key"], cred["user_id"])
        except ValueError as e:
            ctx["error"] = str(e)
    ctx.update(extra)
    return ctx


def _page(status=200, **extra):
    return render_template("zotero/index.html", **_context(**extra)), status


@bp.route("/")
def index():
    return _page()


@bp.route("/push/<review_id>", methods=["POST"])
def push(review_id):
    """Push a review's not-yet-pushed sources into its Zotero collection."""
    cred = get_credential("zotero")
    if not cred:
        return redirect(url_for("zotero.index"))
    review = db.get("literature_reviews", review_id)
    if not review:
        abort(404)
    sources = db.query("sources", "review_id = ?", (review_id,))

    try:
        # Reuse the collection created on a previous push, else make one.
        scope = review.get("scope") or {}
        collection_key = scope.get("zotero_collection")
        if not collection_key:
            collection_key = client.create_collection(
                _review_label(review), cred["api_key"], cred["user_id"])
            scope["zotero_collection"] = collection_key
            db.update("literature_reviews", review_id, {"scope": scope})

        to_push = [s for s in sources
                   if not (s.get("meta") or {}).get("zotero_key")]
        skipped = len(sources) - len(to_push)
        pushed = failed = 0
        if to_push:
            payloads = [client.source_to_item(s, collection_key)
                        for s in to_push]
            result = client.push_items(payloads, cred["api_key"], cred["user_id"])
            failed = result.get("failed", 0)
            for idx, key in (result.get("by_index") or {}).items():
                source = to_push[int(idx)]
                meta = source.get("meta") or {}
                meta["zotero_key"] = key
                db.update("sources", source["id"], {"meta": meta})
                pushed += 1
    except ValueError as e:
        return _page(status=400, error=str(e))

    return _page(push_result={
        "review": _review_label(review),
        "pushed": pushed, "skipped": skipped, "failed": failed,
    })


@bp.route("/pull", methods=["POST"])
def pull():
    """Import a Zotero collection's items as sources on a (new) review."""
    cred = get_credential("zotero")
    if not cred:
        return redirect(url_for("zotero.index"))
    collection_key = request.form.get("collection", "").strip()
    if not collection_key:
        return _page(status=400, error="Pick a Zotero collection to import.")
    target = request.form.get("target", "new").strip() or "new"

    try:
        collections = client.list_collections(cred["api_key"], cred["user_id"])
        collection_name = next(
            (c["name"] for c in collections if c["key"] == collection_key),
            collection_key)
        items = client.collection_items(
            collection_key, cred["api_key"], cred["user_id"])
    except ValueError as e:
        return _page(status=400, error=str(e))

    if target == "new":
        review_id = db.insert("literature_reviews", {
            "project_id": request.form.get("project_id") or None,
            "research_question": f"Imported from Zotero: {collection_name}",
            "scope": {"zotero_collection": collection_key,
                      "imported_from_zotero": True},
            "status": "done",
            "report_md": "",
        })
        review = db.get("literature_reviews", review_id)
    else:
        review = db.get("literature_reviews", target)
        if not review:
            abort(404)
        review_id = review["id"]

    # Dedupe within the target review: DOI first, case-insensitive title second.
    existing = db.query("sources", "review_id = ?", (review_id,), order="")
    seen_dois = {s["doi"].strip().lower() for s in existing if s.get("doi")}
    seen_titles = {s["title"].strip().lower() for s in existing if s.get("title")}

    imported = skipped = 0
    for data in items:
        title = (data.get("title") or "").strip()
        doi = (data.get("DOI") or "").strip()
        doi_l, title_l = doi.lower(), title.lower()
        if (doi_l and doi_l in seen_dois) or (title_l and title_l in seen_titles):
            skipped += 1
            continue
        db.insert("sources", {
            "review_id": review_id,
            "project_id": review.get("project_id"),
            "title": title or "(untitled)",
            "authors": _creators_to_string(data.get("creators")),
            "year": str(data.get("date") or ""),
            "venue": data.get("publicationTitle") or "",
            "url": data.get("url") or "",
            "doi": doi,
            "abstract": data.get("abstractNote") or "",
            "meta": {"zotero_key": data.get("key", "")},
        })
        if doi_l:
            seen_dois.add(doi_l)
        if title_l:
            seen_titles.add(title_l)
        imported += 1

    return _page(pull_result={
        "collection": collection_name,
        "review_id": review_id,
        "review": _review_label(review),
        "new_review": target == "new",
        "imported": imported, "skipped": skipped,
    })
