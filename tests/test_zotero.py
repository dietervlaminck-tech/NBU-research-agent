"""Tests for the Zotero connector.

The live Zotero API is never hit: client functions are monkeypatched. These
tests cover author-string parsing, the push flow (collection creation, item
keys persisted to source.meta, second push skips already-pushed sources),
the pull flow (new review + DOI/title dedupe), and the not-connected page.
"""
import os
import tempfile

os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db, create_app  # noqa: E402

db.init_db()

from nbu_research.credentials import delete_credential, set_credential  # noqa: E402
from nbu_research.modules.zotero import client  # noqa: E402

APP = create_app()
APP.config["TESTING"] = True


# --- parse_authors ------------------------------------------------------------

def test_parse_authors_full_names_comma_separated():
    assert client.parse_authors("Jane Smith, John Doe") == [
        {"creatorType": "author", "firstName": "Jane", "lastName": "Smith"},
        {"creatorType": "author", "firstName": "John", "lastName": "Doe"},
    ]


def test_parse_authors_apa_initials_with_ampersand():
    assert client.parse_authors("Smith, J., & Doe, J.") == [
        {"creatorType": "author", "lastName": "Smith", "firstName": "J."},
        {"creatorType": "author", "lastName": "Doe", "firstName": "J."},
    ]


def test_parse_authors_semicolons_and_and():
    assert client.parse_authors("Smith, Jane; Doe, John") == [
        {"creatorType": "author", "lastName": "Smith", "firstName": "Jane"},
        {"creatorType": "author", "lastName": "Doe", "firstName": "John"},
    ]
    assert client.parse_authors("Jane Smith and John Doe") == [
        {"creatorType": "author", "firstName": "Jane", "lastName": "Smith"},
        {"creatorType": "author", "firstName": "John", "lastName": "Doe"},
    ]


def test_parse_authors_unparseable_falls_back_to_name():
    assert client.parse_authors("Madonna") == [
        {"creatorType": "author", "name": "Madonna"}]
    assert client.parse_authors("") == []
    assert client.parse_authors(None) == []


# --- helpers ------------------------------------------------------------------

def _connect():
    set_credential("zotero", {"api_key": "k", "user_id": "12345"},
                   user_id="dev")


def _make_review(question="Does AI help auditors?"):
    review_id = db.insert("literature_reviews", {
        "research_question": question, "status": "done", "report_md": "",
    })
    s1 = db.insert("sources", {
        "review_id": review_id, "title": "Paper One",
        "authors": "Jane Smith, John Doe", "year": "2021",
        "venue": "J. of Tests", "doi": "10.1/one", "url": "https://x/1",
        "abstract": "First.",
    })
    s2 = db.insert("sources", {
        "review_id": review_id, "title": "Paper Two",
        "authors": "Smith, J., & Doe, J.", "year": "2022",
        "doi": "10.1/two",
    })
    return review_id, s1, s2


# --- not connected ------------------------------------------------------------

def test_not_connected_page_links_to_settings():
    delete_credential("zotero", user_id="dev")
    r = APP.test_client().get("/zotero/")
    assert r.status_code == 200
    assert b"/settings/connections" in r.data
    assert b"Not connected" in r.data


# --- push flow ----------------------------------------------------------------

def test_push_persists_keys_and_second_push_skips(monkeypatch):
    _connect()
    review_id, s1, s2 = _make_review()

    created_collections = []

    def fake_create_collection(name, api_key, user_id):
        created_collections.append((name, api_key, user_id))
        return "COLLKEY1"

    pushed_payloads = []

    def fake_push_items(payloads, api_key, user_id):
        pushed_payloads.extend(payloads)
        by_index = {i: f"ITEM{i}" for i in range(len(payloads))}
        return {"created": list(by_index.values()), "failed": 0,
                "by_index": by_index}

    monkeypatch.setattr(client, "create_collection", fake_create_collection)
    monkeypatch.setattr(client, "push_items", fake_push_items)
    monkeypatch.setattr(client, "list_collections",
                        lambda api_key, user_id: [])

    c = APP.test_client()
    r = c.post(f"/zotero/push/{review_id}")
    assert r.status_code == 200
    assert b"2 pushed" in r.data

    # Collection created once, named after the review, key stored in scope.
    assert created_collections == [("Does AI help auditors?", "k", "12345")]
    review = db.get("literature_reviews", review_id)
    assert review["scope"]["zotero_collection"] == "COLLKEY1"

    # Item payloads carried the collection and parsed creators
    # (payload order follows db.query order — match by title).
    assert all(p["collections"] == ["COLLKEY1"] for p in pushed_payloads)
    assert all(p["itemType"] == "journalArticle" for p in pushed_payloads)
    by_title = {p["title"]: i for i, p in enumerate(pushed_payloads)}
    assert {"creatorType": "author", "firstName": "Jane", "lastName": "Smith"} \
        in pushed_payloads[by_title["Paper One"]]["creators"]

    # Zotero keys persisted into source.meta, mapped to the right source.
    assert db.get("sources", s1)["meta"]["zotero_key"] == \
        f"ITEM{by_title['Paper One']}"
    assert db.get("sources", s2)["meta"]["zotero_key"] == \
        f"ITEM{by_title['Paper Two']}"

    # Second push: nothing left to send — push_items not called again,
    # collection reused (create_collection not called again).
    pushed_payloads.clear()
    r = c.post(f"/zotero/push/{review_id}")
    assert r.status_code == 200
    assert b"0 pushed" in r.data
    assert b"2 already in Zotero" in r.data
    assert pushed_payloads == []
    assert len(created_collections) == 1


# --- pull flow ----------------------------------------------------------------

def test_pull_creates_review_and_dedupes_by_doi(monkeypatch):
    _connect()
    items = [
        {"key": "ZA", "title": "Alpha", "DOI": "10.9/ALPHA",
         "creators": [{"creatorType": "author", "firstName": "Ada",
                       "lastName": "Lovelace"}],
         "date": "2019", "publicationTitle": "Comp. J.",
         "url": "https://x/a", "abstractNote": "A."},
        # Same DOI (different case) — must be skipped as a duplicate.
        {"key": "ZB", "title": "Alpha (reprint)", "DOI": "10.9/alpha",
         "creators": [], "date": "2020"},
        {"key": "ZC", "title": "Beta", "DOI": "",
         "creators": [{"creatorType": "author", "name": "ACME Institute"}],
         "date": "2021"},
    ]
    monkeypatch.setattr(client, "list_collections",
                        lambda api_key, user_id: [{"key": "C1", "name": "My Coll"}])
    monkeypatch.setattr(client, "collection_items",
                        lambda key, api_key, user_id: items)

    r = APP.test_client().post("/zotero/pull",
                               data={"collection": "C1", "target": "new"})
    assert r.status_code == 200
    assert b"1 duplicates skipped" in r.data

    reviews = db.query("literature_reviews",
                       "research_question = ?", ("Imported from Zotero: My Coll",))
    assert len(reviews) == 1
    review = reviews[0]
    assert review["status"] == "done"
    assert review["scope"]["zotero_collection"] == "C1"

    sources = db.query("sources", "review_id = ?", (review["id"],), order="")
    assert len(sources) == 2  # duplicate DOI collapsed
    by_title = {s["title"]: s for s in sources}
    assert by_title["Alpha"]["meta"]["zotero_key"] == "ZA"
    assert by_title["Alpha"]["authors"] == "Ada Lovelace"
    assert by_title["Alpha"]["doi"] == "10.9/ALPHA"
    assert by_title["Beta"]["authors"] == "ACME Institute"

    # Pulling the same collection into the same review skips everything
    # (DOI match for Alpha, case-insensitive title match for Beta).
    r = APP.test_client().post("/zotero/pull",
                               data={"collection": "C1", "target": review["id"]})
    assert r.status_code == 200
    assert b"0 imported" in r.data
    assert len(db.query("sources", "review_id = ?", (review["id"],), order="")) == 2
