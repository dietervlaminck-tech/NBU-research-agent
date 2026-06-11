"""Thin LSEG Refinitiv (lseg-data) client.

⚠️ Verified-by-construction, not live: this environment has no lseg-data
library, no Refinitiv Workspace, and no app key, so the data-fetch path could
not be exercised end-to-end here. It is written against the documented
LSEG Data Library for Python API (package ``lseg.data``, with a fallback to the
older ``refinitiv.data``). The session-open and get_data calls are isolated in
small functions so they're easy to adjust against the installed library version
once credentials are in place. Everything degrades gracefully: if the library
is missing or a call fails, callers get a plain ValueError with a clear message
and the app never crashes.

Session modes come from config (see config.py / docs/REFINITIV_DESIGN.md):
- desktop  → connects to a locally running Workspace (the eikon2 seat).
- platform → machine account (app key + client id/secret), server-capable.
"""
import re

from ...config import (
    LSEG_SESSION, LSEG_APP_KEY, LSEG_CLIENT_ID, LSEG_CLIENT_SECRET,
)


def _import_lib():
    """Return the lseg-data module (or the legacy refinitiv-data), or None."""
    try:
        import lseg.data as rd
        return rd
    except Exception:
        try:
            import refinitiv.data as rd
            return rd
        except Exception:
            return None


def library_available():
    """(ok, reason) — whether the lseg-data Python library is importable."""
    if _import_lib() is None:
        return False, (
            "The lseg-data Python library is not installed. Install it with "
            "`pip install lseg-data` (only needed on a machine/host that will "
            "pull Refinitiv data)."
        )
    return True, ""


def parse_instruments(raw):
    """Split a textarea of RICs/tickers (newline or comma separated)."""
    parts = re.split(r"[\n,]+", raw or "")
    seen, out = set(), []
    for p in parts:
        s = p.strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def open_session():
    """Open and set the default Refinitiv session per config. Returns the
    library module. Raises ValueError with a clear message on failure."""
    rd = _import_lib()
    if rd is None:
        raise ValueError(library_available()[1])
    try:
        if LSEG_SESSION == "platform":
            session = rd.session.platform.Definition(
                app_key=LSEG_APP_KEY,
                client_id=LSEG_CLIENT_ID,
                client_secret=LSEG_CLIENT_SECRET,
            ).get_session()
        else:
            session = rd.session.desktop.Definition(
                app_key=LSEG_APP_KEY,
            ).get_session()
        rd.session.set_default(session)
        session.open()
        return rd
    except Exception as e:  # library/auth/connectivity errors -> user message
        raise ValueError(
            f"Could not open a Refinitiv {LSEG_SESSION} session: {e}. "
            + ("Make sure Refinitiv Workspace is running and you are signed in."
               if LSEG_SESSION == "desktop" else
               "Check the app key and machine-account credentials.")
        )


def _close_quietly(rd):
    try:
        rd.close_session()
    except Exception:
        pass


def build_panel(instruments, fields, start=None, end=None, progress=None):
    """Fetch `fields` for `instruments` into a tidy DataFrame.

    Returns (DataFrame, notes). Columns: Instrument + one per field. When start/
    end are given, the request asks for an annual (FY) series over that range;
    otherwise the latest point-in-time values. Sanitizing into analysis-ready
    column ids happens downstream in datasets.store.from_dataframe.
    """
    import pandas as pd

    notes = []
    if progress:
        progress(0.1, "Opening Refinitiv session…")
    rd = open_session()
    try:
        if progress:
            progress(0.4, f"Requesting {len(fields)} field(s) for "
                          f"{len(instruments)} instrument(s)…")
        params = {}
        if start:
            params["SDate"] = start
        if end:
            params["EDate"] = end
        if start or end:
            params.setdefault("Frq", "FY")  # annual frequency for panels
        df = rd.get_data(
            universe=instruments,
            fields=fields,
            parameters=params or None,
        )
        if progress:
            progress(0.85, "Formatting results…")
        if df is None or len(df) == 0:
            return pd.DataFrame(), ["Refinitiv returned an empty result."]
        # get_data already returns a tidy DataFrame (one row per instrument, or
        # per instrument/period for time series). Hand it back as-is.
        return df.reset_index(drop=True), notes
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Refinitiv data request failed: {e}")
    finally:
        _close_quietly(rd)
