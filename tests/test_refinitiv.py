"""Tests for the Refinitiv connector.

The live lseg-data path can't be exercised here (no library, no Workspace, no
key), so these tests cover what we CAN verify: graceful dormancy when
unconfigured, instrument parsing, and that the panel job stores whatever the
client returns via datasets.store.from_dataframe (client mocked).
"""
import os
import tempfile

import pandas as pd

os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ANTHROPIC_API_KEY", None)
# Ensure unconfigured by default.
for k in ("LSEG_APP_KEY", "LSEG_CLIENT_ID", "LSEG_CLIENT_SECRET"):
    os.environ.pop(k, None)

from nbu_research import db  # noqa: E402
db.init_db()

from nbu_research.modules.refinitiv import client  # noqa: E402
from nbu_research.modules.refinitiv import _run_panel_job  # noqa: E402


def test_parse_instruments():
    assert client.parse_instruments("AAPL.O, MSFT.O\nasml.as") == \
        ["AAPL.O", "MSFT.O", "ASML.AS"]
    assert client.parse_instruments("  ") == []


def test_library_available_reports_cleanly():
    # Must never raise; reason must name the library when it's absent.
    ok, reason = client.library_available()
    assert isinstance(ok, bool)
    if not ok:
        assert "lseg-data" in reason


def test_open_session_unconfigured_raises_valueerror():
    # Whether or not lseg-data is installed, opening a session with no app key
    # configured must surface a plain ValueError (never a raw library error).
    try:
        client.open_session()
        assert False, "expected ValueError"
    except ValueError as e:
        assert "lseg-data" in str(e) or "Refinitiv" in str(e)


def test_panel_job_stores_dataset(monkeypatch):
    """With the client mocked to return a frame, the job must produce a
    refinitiv-sourced dataset via store.from_dataframe."""
    fake = pd.DataFrame({
        "Instrument": ["AAPL.O", "MSFT.O"],
        "Revenue": [383285000000, 211915000000],
        "NetIncome": [96995000000, 72361000000],
    })
    monkeypatch.setattr(client, "build_panel",
                        lambda instruments, fields, start, end, progress=None: (fake, []))
    pid = db.insert("projects", {"title": "Refinitiv test"})
    result = _run_panel_job("jobx", pid, "Rev panel", "desc",
                            ["AAPL.O", "MSFT.O"], ["TR.Revenue", "TR.NetIncome"],
                            None, None)
    ds = db.get("datasets", result["dataset_id"])
    assert ds["source"] == "refinitiv"
    assert ds["n_rows"] == 2
    ids = [c["id"] for c in ds["columns"]]
    assert "revenue" in ids and "netincome" in ids
    # And the stored dataset must be analysis-ready (polymorphic loaders accept it)
    from nbu_research.modules.analysis import quantitative
    dfr = quantitative.responses_dataframe(ds)
    assert len(dfr) == 2


if __name__ == "__main__":
    import types
    mp = types.SimpleNamespace(setattr=lambda o, n, v: setattr(o, n, v))
    test_parse_instruments()
    test_library_available_reports_missing()
    test_open_session_without_library_raises_valueerror()
    test_panel_job_stores_dataset(mp)
    print("all refinitiv tests passed")
