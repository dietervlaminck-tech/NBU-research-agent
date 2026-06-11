"""SPSS .sav export for survey studies via pyreadstat."""
import os
import re
import tempfile

import pandas as pd
import pyreadstat

from ... import db
from .common import slugify, survey_columns, answer_value, question_id

NUMERIC_TYPES = {"likert", "numeric", "matrix"}


def sanitize_varnames(names, maxlen=64):
    """Stat-package variable names: <=maxlen bytes (SPSS 64, Stata 32),
    alnum/underscore, start with a letter, unique (case-insensitive).
    Returns a list aligned with `names`."""
    out, seen = [], set()
    for name in names:
        s = re.sub(r"[^0-9A-Za-z_]", "_", str(name)) or "var"
        if not s[0].isalpha():
            s = "v_" + s
        s = s[:maxlen]
        candidate, n = s, 1
        while candidate.lower() in seen:
            n += 1
            suffix = f"_{n}"
            candidate = s[:maxlen - len(suffix)] + suffix
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def study_sav(study_id):
    study = db.get("studies", study_id) or {}
    questions = (study.get("config") or {}).get("questions") or []
    responses = db.query("survey_responses", "study_id = ?",
                         (study.get("id", study_id),), order="started_at")
    cols = survey_columns(questions)
    qids = {id(q): question_id(q, i) for i, q in enumerate(questions)}

    raw_names = ["response_id", "respondent_name"] + [c[0] for c in cols]
    labels = ["Response ID", "Respondent name"] + [c[1][:256] for c in cols]
    varnames = sanitize_varnames(raw_names)

    numeric = [False, False] + [c[2].get("type") in NUMERIC_TYPES for c in cols]

    rows = []
    for r in responses:
        row = [r.get("id"), r.get("respondent_name", "")]
        for _key, _label, q, mrow in cols:
            row.append(answer_value(q, mrow, r.get("answers"), qids[id(q)]))
        rows.append(row)
    df = pd.DataFrame(rows, columns=varnames)

    value_labels = {}
    for vn, (_key, _label, q, _mrow) in zip(varnames[2:], cols):
        scale = q.get("scale") or {}
        if q.get("type") in ("likert", "matrix"):
            vl = {}
            if scale.get("min_label") is not None and scale.get("min") is not None:
                vl[float(scale["min"])] = str(scale["min_label"])
            if scale.get("max_label") is not None and scale.get("max") is not None:
                vl[float(scale["max"])] = str(scale["max_label"])
            if vl:
                value_labels[vn] = vl

    for vn, is_num in zip(varnames, numeric):
        if is_num:
            df[vn] = pd.to_numeric(df[vn], errors="coerce").astype(float)
        else:
            df[vn] = df[vn].fillna("").astype(str)

    fd, path = tempfile.mkstemp(suffix=".sav")
    os.close(fd)
    try:
        pyreadstat.write_sav(
            df, path,
            file_label=study.get("title", "")[:64],
            column_labels=labels,
            variable_value_labels=value_labels or None,
        )
        with open(path, "rb") as f:
            data = f.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
    return data, f"{slugify(study.get('title'))}.sav", "application/x-spss-sav"
