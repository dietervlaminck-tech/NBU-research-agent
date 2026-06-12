"""Qualtrics legacy-CSV pre-processing for the datasets upload path.

Qualtrics' legacy CSV export has THREE header rows:
  row 1: column ids (QID-ish names like Q1, Q2_1, plus metadata columns)
  row 2: full question text (human-readable labels)
  row 3: ImportId JSON, e.g. {"ImportId":"QID1"}

Detection rule: the file has at least 3 rows and row 3 contains the literal
'"ImportId"' in some cell. When detected, row 1 becomes the column ids, row 2
the labels (passed through to the dataset column spec), rows 2-3 are skipped
from the data, and Qualtrics' own metadata columns are dropped (unless they
are the only columns in the file).
"""
import csv
import io

import pandas as pd

# Metadata columns Qualtrics prepends/appends to every export.
_META_EXACT = {
    "StartDate", "EndDate", "Status", "IPAddress", "Progress", "Finished",
    "RecordedDate", "ResponseId", "ExternalReference", "DistributionChannel",
    "UserLanguage",
}


def _is_metadata_column(name):
    n = str(name).strip()
    return (
        n in _META_EXACT
        or n.startswith("Duration")      # "Duration (in seconds)"
        or n.startswith("Recipient")     # RecipientLastName/FirstName/Email
        or "Location" in n               # LocationLatitude/Longitude/…
    )


def detect(text):
    """True when CSV text looks like a Qualtrics legacy 3-header-row export."""
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [next(reader) for _ in range(3)]
    except (StopIteration, csv.Error):
        return False
    return any('"ImportId"' in (cell or "") for cell in rows[2])


def parse(text):
    """Parse a detected Qualtrics CSV.

    Returns (df, labels): df with row-1 ids as columns and rows 2-3 skipped,
    metadata columns dropped (unless nothing else remains); labels maps each
    remaining column id to its row-2 question text.
    """
    reader = csv.reader(io.StringIO(text))
    id_row = next(reader)
    label_row = next(reader)
    all_labels = {cid: (label or cid) for cid, label in zip(id_row, label_row)}

    df = pd.read_csv(io.StringIO(text), skiprows=[1, 2])

    keep = [c for c in df.columns if not _is_metadata_column(c)]
    if keep:
        df = df[keep]

    labels = {str(c): all_labels.get(str(c), str(c)) for c in df.columns}
    return df, labels
