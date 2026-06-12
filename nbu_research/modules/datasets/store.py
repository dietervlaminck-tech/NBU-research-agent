"""Canonical dataset store.

Turns a pandas DataFrame into a `datasets` row with an analysis-safe column
spec and CSV body, matching the shape `analysis.quantitative.dataframe_columns`
returns. Connectors (EDGAR, Refinitiv) and the upload route all funnel through
`from_dataframe` so column typing stays consistent across the platform.
"""
import io
import re

import pandas as pd
from pandas.api import types as ptypes

from ... import db

# Share the slug conventions used by the analysis module so dataset column ids
# line up with what the stat functions expect (lowercase, [a-z0-9_]).
from ..analysis.quantitative import _slug


def _safe_id(text):
    """Analysis-safe column id: _slug, then guarantee it starts with a letter."""
    s = _slug(text)
    if not re.match(r"^[a-z]", s):
        s = "v_" + s
    return s


def _unique_ids(labels):
    """Map each original label to a unique analysis-safe id, deduping collisions."""
    ids, seen = [], {}
    for label in labels:
        base = _safe_id(label)
        cid = base
        if cid in seen:
            seen[base] += 1
            cid = f"{base}_{seen[base]}"
            while cid in seen:
                seen[base] += 1
                cid = f"{base}_{seen[base]}"
        seen[cid] = 0
        ids.append(cid)
    return ids


def _looks_numeric(series):
    """True when an object/text column is mostly numbers (>80% of non-null
    values parse as floats), so it should be coerced to numeric."""
    non_null = series.dropna()
    if non_null.empty:
        return False
    parsed = pd.to_numeric(non_null, errors="coerce")
    return parsed.notna().mean() > 0.80


def _infer_kind(series):
    """Return ("numeric"|"categorical", maybe-coerced-series)."""
    if ptypes.is_bool_dtype(series):
        return "numeric", series.astype("float64")
    if ptypes.is_numeric_dtype(series):
        return "numeric", series
    if _looks_numeric(series):
        return "numeric", pd.to_numeric(series, errors="coerce")
    return "categorical", series


def from_dataframe(project_id, name, df, source="upload", source_meta=None,
                   description="", labels=None):
    """Infer the column spec from a pandas DataFrame, store as a dataset row,
    return the new dataset id.

    `labels` (optional, backward-compatible) maps original column names to
    human-readable labels for the column spec — e.g. Qualtrics question text.
    Columns missing from the map keep the original name as label."""
    df = df.copy()
    ids = _unique_ids([str(c) for c in df.columns])
    columns = []
    new_data = {}
    for cid, orig in zip(ids, df.columns):
        kind, series = _infer_kind(df[orig])
        label = (labels or {}).get(str(orig)) or str(orig)
        columns.append({"id": cid, "label": label, "kind": kind})
        new_data[cid] = series.reset_index(drop=True)

    # Rebuild with sanitized ids so the CSV header matches the column ids.
    out = pd.DataFrame(new_data, columns=ids)
    data_csv = out.to_csv(index=False)

    return db.insert("datasets", {
        "project_id": project_id or None,
        "name": name or "Untitled dataset",
        "description": description or "",
        "source": source or "upload",
        "source_meta": source_meta or {},
        "columns": columns,
        "data_csv": data_csv,
        "n_rows": int(len(out)),
        "status": "ready",
    })


def dataframe(dataset):
    """Parse a stored dataset's CSV back into a DataFrame (columns = ids),
    coercing numeric columns per the stored spec."""
    text = dataset.get("data_csv") or ""
    cols = dataset.get("columns") or []
    if not text.strip():
        return pd.DataFrame(columns=[c["id"] for c in cols])
    df = pd.read_csv(io.StringIO(text))
    for c in cols:
        if c.get("kind") == "numeric" and c["id"] in df.columns:
            df[c["id"]] = pd.to_numeric(df[c["id"]], errors="coerce")
    return df


def preview(dataset, n=20):
    """Return (column_ids, rows) for the UI, rows being a list of dicts keyed by
    column id, covering the first `n` rows."""
    df = dataframe(dataset)
    col_ids = [c["id"] for c in (dataset.get("columns") or [])]
    head = df.head(n)
    rows = []
    for _, r in head.iterrows():
        row = {}
        for cid in col_ids:
            v = r.get(cid) if cid in head.columns else None
            row[cid] = "" if pd.isna(v) else v
        rows.append(row)
    return col_ids, rows
