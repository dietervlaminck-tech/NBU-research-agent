"""Tests for the datasets store.

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research so datasets land
in a throwaway SQLite database, and ensures no API key leaks in (the analysis
LLM interpretation must degrade to None — but we don't run analyses here).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="nbu_test_datasets_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)

import pandas as pd  # noqa: E402

from nbu_research import db  # noqa: E402
from nbu_research.modules.datasets import store  # noqa: E402
from nbu_research.modules.analysis import quantitative as q  # noqa: E402

db.init_db()  # create the schema in the throwaway database


def _make_dataframe():
    return pd.DataFrame({
        "Age (years)": [21, 34, 45, 28, 39],
        "Annual Income": ["50000", "62000", "71000", "48000", "90000"],  # numeric-looking object
        "Department": ["Sales", "Eng", "Sales", "HR", "Eng"],
        "Is Manager": [True, False, True, False, True],
    })


def test_column_spec_kinds():
    ds_id = store.from_dataframe(None, "Test DS", _make_dataframe())
    dataset = db.get("datasets", ds_id)
    kinds = {c["id"]: c["kind"] for c in dataset["columns"]}
    labels = {c["id"]: c["label"] for c in dataset["columns"]}

    # Sanitized ids: lowercase, [a-z0-9_], start with a letter.
    for cid in kinds:
        assert cid[0].isalpha() and cid.islower()
        assert all(ch.isalnum() or ch == "_" for ch in cid)

    ids = list(kinds)
    assert kinds[ids[0]] == "numeric"       # Age — numeric dtype
    assert kinds[ids[1]] == "numeric"       # Annual Income — numeric-looking object
    assert kinds[ids[2]] == "categorical"   # Department — text
    assert kinds[ids[3]] == "numeric"       # Is Manager — bool -> numeric

    # Original names preserved as labels.
    assert "Age (years)" in labels.values()
    assert "Department" in labels.values()


def test_data_csv_roundtrips_with_sanitized_ids():
    ds_id = store.from_dataframe(None, "Roundtrip", _make_dataframe())
    dataset = db.get("datasets", ds_id)
    df = store.dataframe(dataset)
    col_ids = [c["id"] for c in dataset["columns"]]

    assert list(df.columns) == col_ids
    assert len(df) == dataset["n_rows"] == 5

    # Numeric-looking object column is parsed as real numbers on the way back.
    income_id = [c["id"] for c in dataset["columns"] if c["label"] == "Annual Income"][0]
    assert pd.api.types.is_numeric_dtype(df[income_id])
    assert df[income_id].iloc[0] == 50000


def test_dedup_collisions():
    df = pd.DataFrame({"X!": [1, 2], "X?": [3, 4]})
    ds_id = store.from_dataframe(None, "Dedup", df)
    dataset = db.get("datasets", ds_id)
    ids = [c["id"] for c in dataset["columns"]]
    assert len(ids) == len(set(ids))  # no collisions


def test_single_column_and_all_numeric():
    df = pd.DataFrame({"only": [1.5, 2.5, 3.5]})
    ds_id = store.from_dataframe(None, "Single", df)
    dataset = db.get("datasets", ds_id)
    assert len(dataset["columns"]) == 1
    assert dataset["columns"][0]["kind"] == "numeric"


def test_preview_shape():
    ds_id = store.from_dataframe(None, "Prev", _make_dataframe())
    dataset = db.get("datasets", ds_id)
    col_ids, rows = store.preview(dataset, n=3)
    assert col_ids == [c["id"] for c in dataset["columns"]]
    assert len(rows) == 3
    assert set(rows[0].keys()) == set(col_ids)


def test_analysis_accepts_stored_dataset_row():
    """dataframe_columns and responses_dataframe from analysis.quantitative must
    accept the stored dataset row directly (polymorphic with surveys)."""
    ds_id = store.from_dataframe(None, "Analyze", _make_dataframe())
    dataset = db.get("datasets", ds_id)

    cols = q.dataframe_columns(dataset)
    assert cols == dataset["columns"]

    df = q.responses_dataframe(dataset)
    assert list(df.columns) == [c["id"] for c in dataset["columns"]]
    assert len(df) == 5

    # A numeric column should be analyzable straight away.
    numeric_ids = [c["id"] for c in cols if c["kind"] == "numeric"]
    assert numeric_ids
    res = q.descriptives(dataset, {})
    assert res["n_responses"] == 5
