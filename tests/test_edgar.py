"""Unit tests for the EDGAR connector's panel-assembly logic.

No real network: client functions are monkeypatched with tiny mocked
submissions/companyfacts-derived data, and datasets.store.from_dataframe is
stubbed to capture the DataFrame the connector hands it.
"""
import os
import sys
import tempfile

import pandas as pd
import pytest

# Point the DB at a throwaway location before importing the app package.
os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp(prefix="edgar_test_"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nbu_research.modules.edgar as edgar  # noqa: E402
from nbu_research.modules.edgar import client  # noqa: E402


# Mocked per-company XBRL data: ticker -> {concept -> [annual points]}.
MOCK = {
    "AAPL": {
        "cik": "0000320193",
        "title": "Apple Inc.",
        "Revenues": [
            {"end": "2022-09-24", "val": 394328000000, "fy": 2022, "fp": "FY", "form": "10-K"},
            {"end": "2023-09-30", "val": 383285000000, "fy": 2023, "fp": "FY", "form": "10-K"},
        ],
        "NetIncomeLoss": [
            {"end": "2022-09-24", "val": 99803000000, "fy": 2022, "fp": "FY", "form": "10-K"},
            {"end": "2023-09-30", "val": 96995000000, "fy": 2023, "fp": "FY", "form": "10-K"},
        ],
    },
    "MSFT": {
        "cik": "0000789019",
        "title": "Microsoft Corporation",
        "Revenues": [
            {"end": "2023-06-30", "val": 211915000000, "fy": 2023, "fp": "FY", "form": "10-K"},
        ],
        # No NetIncomeLoss on purpose -> NaN cell expected.
    },
}


@pytest.fixture
def patched(monkeypatch):
    def ticker_to_cik(ticker):
        t = ticker.upper()
        if t not in MOCK:
            raise ValueError(f"Ticker '{t}' not found in SEC EDGAR")
        return MOCK[t]["cik"], MOCK[t]["title"]

    def company_facts(cik10):
        # Return a marker we can map back to a ticker in concept_series.
        for t, d in MOCK.items():
            if d["cik"] == cik10:
                return {"_ticker": t}
        raise ValueError("not found")

    def concept_series(cik10, concept, unit="USD", facts=None):
        ticker = (facts or {}).get("_ticker")
        if ticker is None:
            for t, d in MOCK.items():
                if d["cik"] == cik10:
                    ticker = t
        return MOCK.get(ticker, {}).get(concept, [])

    monkeypatch.setattr(client, "ticker_to_cik", ticker_to_cik)
    monkeypatch.setattr(client, "company_facts", company_facts)
    monkeypatch.setattr(client, "concept_series", concept_series)
    return monkeypatch


def test_build_panel_shape_and_columns(patched):
    concepts = ["Revenues", "NetIncomeLoss"]
    df, notes = edgar.build_panel(
        ["AAPL", "MSFT"], concepts, year_from=None, year_to=None,
    )
    # Columns: identity columns + one per concept.
    assert list(df.columns) == ["ticker", "company", "fiscal_year", "Revenues", "NetIncomeLoss"]
    # AAPL 2022 + 2023, MSFT 2023 = 3 rows.
    assert len(df) == 3
    assert set(df["ticker"]) == {"AAPL", "MSFT"}

    # MSFT has no NetIncomeLoss -> NaN.
    msft_2023 = df[(df["ticker"] == "MSFT") & (df["fiscal_year"] == 2023)].iloc[0]
    assert pd.isna(msft_2023["NetIncomeLoss"])
    assert msft_2023["Revenues"] == 211915000000


def test_build_panel_year_filter(patched):
    df, _ = edgar.build_panel(["AAPL"], ["Revenues"], year_from=2023, year_to=2023)
    assert len(df) == 1
    assert int(df.iloc[0]["fiscal_year"]) == 2023


def test_build_panel_skips_unresolved_ticker(patched):
    df, notes = edgar.build_panel(["AAPL", "NOPE"], ["Revenues"], None, None)
    assert set(df["ticker"]) == {"AAPL"}
    assert any("NOPE" in n for n in notes)


def test_run_panel_job_calls_from_dataframe(patched, monkeypatch):
    captured = {}

    def fake_from_dataframe(project_id, name, df, source="upload",
                            source_meta=None, description=""):
        captured["df"] = df
        captured["source"] = source
        captured["source_meta"] = source_meta
        captured["name"] = name
        return "ds_test123"

    progress_calls = []
    monkeypatch.setattr(edgar, "from_dataframe", fake_from_dataframe)
    monkeypatch.setattr(edgar, "update_progress",
                        lambda jid, *a, **k: progress_calls.append((a, k)))

    result = edgar._run_panel_job(
        "job1", project_id=None, name="My panel", description="desc",
        tickers=["AAPL"], concepts=["Revenues"], year_from=None, year_to=None,
    )

    assert result["dataset_id"] == "ds_test123"
    assert captured["source"] == "edgar"
    assert captured["source_meta"]["tickers"] == ["AAPL"]
    assert captured["source_meta"]["concepts"] == ["Revenues"]
    assert isinstance(captured["df"], pd.DataFrame)
    assert "Revenues" in captured["df"].columns
    assert len(captured["df"]) == 2  # AAPL 2022 + 2023
    assert progress_calls  # progress was reported


def test_run_panel_job_raises_when_empty(patched, monkeypatch):
    monkeypatch.setattr(edgar, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(edgar, "from_dataframe", lambda *a, **k: "x")
    with pytest.raises(ValueError):
        edgar._run_panel_job(
            "job2", None, "n", "d", tickers=["NOPE"], concepts=["Revenues"],
            year_from=None, year_to=None,
        )


def test_parse_tickers():
    assert edgar._parse_tickers("aapl, msft\ngoogl") == ["AAPL", "MSFT", "GOOGL"]
    assert edgar._parse_tickers("AAPL\nAAPL\n") == ["AAPL"]  # dedup
    assert edgar._parse_tickers("") == []
