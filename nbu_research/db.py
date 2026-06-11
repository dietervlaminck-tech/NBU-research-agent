import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone

from .config import DB_PATH


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(length=12):
    return uuid.uuid4().hex[:length]


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
-- Researchers (Entra ID identities). id = the Azure object id (OID).
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);

-- Project collaboration roles: viewer < collaborator < owner.
CREATE TABLE IF NOT EXISTS project_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('viewer', 'collaborator', 'owner')),
    added_at TEXT NOT NULL DEFAULT ''
);

-- Invitations for people who have not logged in yet; converted to a
-- project_members row on their first login (matched by email).
CREATE TABLE IF NOT EXISTS project_invites (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    invited_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    research_question TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

-- A study is one data-collection instrument: an AI interview or a survey.
CREATE TABLE IF NOT EXISTS studies (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    study_type TEXT NOT NULL CHECK (study_type IN ('interview', 'survey')),
    title TEXT NOT NULL,
    research_question TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

-- Interview sessions (one respondent conversation).
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id),
    respondent_name TEXT NOT NULL DEFAULT '',
    messages TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_seconds REAL DEFAULT 0
);

-- Survey responses (one respondent submission).
CREATE TABLE IF NOT EXISTS survey_responses (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id),
    respondent_name TEXT NOT NULL DEFAULT '',
    answers TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'started',
    started_at TEXT NOT NULL,
    completed_at TEXT
);

-- Qualitative analysis: codebooks and coded segments (REFI-QDA exportable).
CREATE TABLE IF NOT EXISTS codebooks (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id),
    name TEXT NOT NULL,
    codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coded_segments (
    id TEXT PRIMARY KEY,
    codebook_id TEXT NOT NULL REFERENCES codebooks(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    code_id TEXT NOT NULL,
    text TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Datasets: first-class analyzable tabular data, from uploads or connectors
-- (SEC EDGAR, Refinitiv). Stored as CSV text + a column spec so the analysis
-- and export suites can target a dataset exactly like a survey.
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'upload',   -- upload | edgar | refinitiv
    source_meta TEXT NOT NULL DEFAULT '{}',
    columns TEXT NOT NULL DEFAULT '[]',      -- [{"id","label","kind"}] kind=numeric|categorical
    data_csv TEXT NOT NULL DEFAULT '',
    n_rows INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL
);

-- Analyses: qualitative (thematic) and quantitative (stats) runs. A run targets
-- exactly one of study_id or dataset_id.
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    study_id TEXT REFERENCES studies(id),
    dataset_id TEXT REFERENCES datasets(id),
    project_id TEXT REFERENCES projects(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    results TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Literature / desk research runs.
CREATE TABLE IF NOT EXISTS literature_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    research_question TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    report_md TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    review_id TEXT REFERENCES literature_reviews(id),
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Article drafts produced by the writing pipeline.
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    article_type TEXT NOT NULL DEFAULT 'empirical',
    status TEXT NOT NULL DEFAULT 'draft',
    outline_md TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Background jobs (agent pipelines run minutes; the UI polls these).
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_table TEXT NOT NULL DEFAULT '',
    ref_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()
    _column_cache.clear()


def _migrate(conn):
    """Additive migrations for databases created before a column existed.
    CREATE TABLE IF NOT EXISTS never adds columns to an existing table."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(analyses)")}
    if "dataset_id" not in cols:
        conn.execute("ALTER TABLE analyses ADD COLUMN dataset_id TEXT")


# --- generic row helpers -----------------------------------------------------

JSON_FIELDS = {
    "studies": ["config"],
    "sessions": ["messages"],
    "survey_responses": ["answers"],
    "codebooks": ["codes"],
    "datasets": ["columns", "source_meta"],
    "analyses": ["params", "results"],
    "literature_reviews": ["scope"],
    "articles": ["metadata"],
    "jobs": ["result"],
}


def _decode(table, row):
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS.get(table, []):
        if isinstance(d.get(f), str):
            try:
                d[f] = json.loads(d[f])
            except (ValueError, TypeError):
                pass
    return d


def insert(table, values):
    values = dict(values)
    values.setdefault("id", new_id())
    if "created_at" in _columns(table):
        values.setdefault("created_at", now())
    if "updated_at" in _columns(table):
        values.setdefault("updated_at", now())
    for f in JSON_FIELDS.get(table, []):
        if f in values and not isinstance(values[f], str):
            values[f] = json.dumps(values[f])
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn = get_db()
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    conn.commit()
    conn.close()
    return values["id"]


def update(table, row_id, values):
    values = dict(values)
    if "updated_at" in _columns(table):
        values.setdefault("updated_at", now())
    for f in JSON_FIELDS.get(table, []):
        if f in values and not isinstance(values[f], str):
            values[f] = json.dumps(values[f])
    sets = ", ".join(f"{k} = ?" for k in values)
    conn = get_db()
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", list(values.values()) + [row_id])
    conn.commit()
    conn.close()


def get(table, row_id):
    conn = get_db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    return _decode(table, row)


def query(table, where="", args=(), order="created_at DESC"):
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [_decode(table, r) for r in rows]


def delete(table, row_id):
    conn = get_db()
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


_column_cache = {}


def _columns(table):
    if table not in _column_cache:
        conn = get_db()
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        conn.close()
        _column_cache[table] = [r["name"] for r in rows]
    return _column_cache[table]
