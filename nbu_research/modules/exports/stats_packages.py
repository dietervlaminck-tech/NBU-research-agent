"""Stata, R, and Python exports plus per-analysis replication packages.

Stakeholder request (M. Erkens): researchers work in Stata/R/Python, not just
SPSS. Two layers:

- Study-level datasets: native Stata (.dta), R (.rds), and a ready-to-run
  Jupyter notebook with the data embedded.
- Analysis-level replication: every quantitative analysis run in the platform
  can be downloaded as the equivalent Stata .do file, R script, or Python
  script, or as a complete replication package (data + all three scripts +
  codebook + README) ready for OSF or a journal's data policy.

The replication data matrix must match the analysis module's dataframe
byte-for-byte (its column ids appear in the scripts), so this module reads the
analysis module's dataframe builder. This is a sanctioned one-way exception to
the no-cross-module-imports rule, documented in docs/INTERFACES.md.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile

import pandas as pd
import pyreadstat

from ... import db
from .common import slugify, survey_columns, answer_value, question_id
from .spss import sanitize_varnames, NUMERIC_TYPES
from ..analysis.quantitative import responses_dataframe, dataframe_columns


# --- study-level dataset exports ---------------------------------------------

def _survey_frame(study, name_maxlen=64):
    """(df, column_labels) for a survey study, mirroring the .sav export."""
    questions = (study.get("config") or {}).get("questions") or []
    responses = db.query("survey_responses", "study_id = ?",
                         (study["id"],), order="started_at")
    cols = survey_columns(questions)
    qids = {id(q): question_id(q, i) for i, q in enumerate(questions)}

    raw_names = ["response_id", "respondent_name"] + [c[0] for c in cols]
    labels = ["Response ID", "Respondent name"] + [c[1][:80] for c in cols]
    varnames = sanitize_varnames(raw_names, maxlen=name_maxlen)
    numeric = [False, False] + [c[2].get("type") in NUMERIC_TYPES for c in cols]

    rows = []
    for r in responses:
        row = [r.get("id"), r.get("respondent_name", "")]
        for _key, _label, q, mrow in cols:
            row.append(answer_value(q, mrow, r.get("answers"), qids[id(q)]))
        rows.append(row)
    df = pd.DataFrame(rows, columns=varnames)
    for vn, is_num in zip(varnames, numeric):
        if is_num:
            df[vn] = pd.to_numeric(df[vn], errors="coerce").astype(float)
        else:
            df[vn] = df[vn].fillna("").astype(str)
    return df, labels


def _via_tempfile(writer, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        writer(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(path):
            os.remove(path)


def study_dta(study_id):
    """Native Stata dataset (variable names <=32 chars, labels preserved)."""
    study = db.get("studies", study_id) or {}
    df, labels = _survey_frame(study, name_maxlen=32)
    data = _via_tempfile(
        lambda p: pyreadstat.write_dta(df, p, column_labels=labels), ".dta")
    return data, f"{slugify(study.get('title'))}.dta", "application/x-stata-dta"


def study_rds(study_id):
    """Native R dataset; load with readRDS().

    pyreadr's librdata clashes with pyreadstat's readstat when both are loaded
    in one process (segfault), so the .rds write runs in a subprocess.
    """
    study = db.get("studies", study_id) or {}
    df, _labels = _survey_frame(study)
    if df.empty:
        # pyreadr's librdata segfaults on zero-row frames; ship an honest
        # placeholder so the export stays a valid, loadable RDS.
        df = pd.DataFrame({"note": ["This survey has no responses yet."]})
    fd_csv, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd_csv)
    fd_rds, rds_path = tempfile.mkstemp(suffix=".rds")
    os.close(fd_rds)
    try:
        df.to_csv(csv_path, index=False)
        script = (
            "import sys, pandas, pyreadr; "
            "df = pandas.read_csv(sys.argv[1]); "
            "pyreadr.write_rds(sys.argv[2], df)"
        )
        subprocess.run([sys.executable, "-c", script, csv_path, rds_path],
                       check=True, capture_output=True, timeout=120)
        with open(rds_path, "rb") as f:
            data = f.read()
    finally:
        for p in (csv_path, rds_path):
            if os.path.exists(p):
                os.remove(p)
    return data, f"{slugify(study.get('title'))}.rds", "application/octet-stream"


def study_ipynb(study_id):
    """Self-contained Jupyter notebook: data embedded + starter analysis."""
    study = db.get("studies", study_id) or {}
    df = responses_dataframe(study)
    csv_text = df.to_csv(index=False)
    numeric_cols = [c for c in df.columns if df[c].dtype.kind in "fi"]

    cells = [
        _md_cell(f"# {study.get('title', 'Survey data')}\n\n"
                 f"Research question: {study.get('research_question', '—')}\n\n"
                 f"Exported from NBU Research Agent. The dataset is embedded "
                 f"below ({len(df)} responses); no external files needed."),
        _code_cell(
            "import io\nimport pandas as pd\n\n"
            f"CSV_DATA = '''{csv_text}'''\n\n"
            "df = pd.read_csv(io.StringIO(CSV_DATA))\ndf.head()"),
        _md_cell("## Descriptives"),
        _code_cell("df.describe(include='all').T"),
    ]
    if len(numeric_cols) >= 2:
        cells += [
            _md_cell("## Correlations (numeric items)"),
            _code_cell(f"df[{numeric_cols!r}].corr().round(3)"),
        ]
    cells.append(_md_cell(
        "## Extend from here\n\n"
        "`statsmodels` examples:\n\n"
        "```python\nimport statsmodels.formula.api as smf\n"
        "model = smf.ols('dv ~ iv1 + iv2', data=df).fit()\n"
        "print(model.summary())\n```"))

    nb = {"cells": cells, "metadata": {"language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    data = json.dumps(nb, indent=1).encode("utf-8")
    return data, f"{slugify(study.get('title'))}.ipynb", "application/x-ipynb+json"


def _md_cell(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text}


def _code_cell(code):
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": code}


# --- analysis-level replication scripts ---------------------------------------

DATA_FILE = "data.csv"


def _analysis_context(analysis_id):
    analysis = db.get("analyses", analysis_id)
    if not analysis:
        raise ValueError("Analysis not found")
    study = db.get("studies", analysis["study_id"]) or {}
    params = analysis.get("params") or {}
    return analysis, study, params


def _stata_script(analysis, study, params):
    kind = analysis["kind"]
    head = (f"* {analysis.get('title', kind)}\n"
            f"* Study: {study.get('title', '')}\n"
            f"* Generated by NBU Research Agent — reproduces the platform analysis.\n\n"
            f'import delimited "{DATA_FILE}", clear varnames(1)\n\n')
    items = params.get("items") or []
    body = {
        "descriptives": "summarize\n",
        "reliability": f"alpha {' '.join(items)}, item\n",
        "ttest": f"ttest {params.get('dv')}, by({params.get('group_col')})\n"
                 f"esize twosample {params.get('dv')}, by({params.get('group_col')}) cohensd\n",
        "anova": f"oneway {params.get('dv')} {params.get('factor')}, tabulate\n",
        "correlation": f"pwcorr {' '.join(items)}, sig obs\n",
        "regression": f"regress {params.get('dv')} {' '.join(params.get('ivs') or [])}\n",
        "crosstab": f"tabulate {params.get('var_a')} {params.get('var_b')}, chi2 V\n",
    }.get(kind, "* (no Stata equivalent for this analysis kind)\n")
    return head + body


def _r_script(analysis, study, params):
    kind = analysis["kind"]
    head = (f"# {analysis.get('title', kind)}\n"
            f"# Study: {study.get('title', '')}\n"
            f"# Generated by NBU Research Agent — reproduces the platform analysis.\n\n"
            f'df <- read.csv("{DATA_FILE}")\n\n')
    items = params.get("items") or []
    vec = "c(" + ", ".join(f'"{i}"' for i in items) + ")"
    ivs = " + ".join(params.get("ivs") or [])
    body = {
        "descriptives": "summary(df)\n",
        "reliability": f"# install.packages('psych')\npsych::alpha(df[, {vec}])\n",
        "ttest": f"t.test({params.get('dv')} ~ {params.get('group_col')}, data = df, var.equal = TRUE)\n"
                 f"# install.packages('effectsize')\n"
                 f"effectsize::cohens_d({params.get('dv')} ~ {params.get('group_col')}, data = df)\n",
        "anova": f"summary(aov({params.get('dv')} ~ {params.get('factor')}, data = df))\n",
        "correlation": f"# install.packages('Hmisc')\nHmisc::rcorr(as.matrix(df[, {vec}]))\n",
        "regression": f"summary(lm({params.get('dv')} ~ {ivs}, data = df))\n",
        "crosstab": f"tab <- table(df${params.get('var_a')}, df${params.get('var_b')})\n"
                    f"tab\nchisq.test(tab)\n",
    }.get(kind, "# (no R equivalent for this analysis kind)\n")
    return head + body


def _python_script(analysis, study, params):
    kind = analysis["kind"]
    head = (f"# {analysis.get('title', kind)}\n"
            f"# Study: {study.get('title', '')}\n"
            f"# Generated by NBU Research Agent — reproduces the platform analysis.\n"
            f"import pandas as pd\nfrom scipy import stats\n\n"
            f"df = pd.read_csv('{DATA_FILE}')\n\n")
    items = params.get("items") or []
    body = {
        "descriptives": "print(df.describe(include='all').T)\n",
        "reliability": (
            f"items = df[{items!r}].dropna()\n"
            "k = items.shape[1]\n"
            "alpha = k / (k - 1) * (1 - items.var(ddof=1).sum() / items.sum(axis=1).var(ddof=1))\n"
            "print(f'Cronbach alpha = {alpha:.3f}')\n"),
        "ttest": (
            f"groups = [g.dropna() for _, g in df.groupby('{params.get('group_col')}')['{params.get('dv')}']]\n"
            "t, p = stats.ttest_ind(*groups)\nprint(f't = {t:.3f}, p = {p:.4f}')\n"),
        "anova": (
            f"groups = [g.dropna() for _, g in df.groupby('{params.get('factor')}')['{params.get('dv')}']]\n"
            "F, p = stats.f_oneway(*groups)\nprint(f'F = {F:.3f}, p = {p:.4f}')\n"),
        "correlation": f"print(df[{items!r}].corr().round(3))\n",
        "regression": (
            "import statsmodels.formula.api as smf\n"
            f"model = smf.ols('{params.get('dv')} ~ {' + '.join(params.get('ivs') or [])}', data=df).fit()\n"
            "print(model.summary())\n"),
        "crosstab": (
            f"tab = pd.crosstab(df['{params.get('var_a')}'], df['{params.get('var_b')}'])\n"
            "print(tab)\nchi2, p, dof, _ = stats.chi2_contingency(tab)\n"
            "print(f'chi2 = {chi2:.3f}, df = {dof}, p = {p:.4f}')\n"),
    }.get(kind, "# (no Python equivalent for this analysis kind)\n")
    return head + body


def _script_export(analysis_id, builder, ext, mimetype):
    analysis, study, params = _analysis_context(analysis_id)
    text = builder(analysis, study, params)
    name = f"{slugify(analysis.get('title') or analysis['kind'])}{ext}"
    return text.encode("utf-8"), name, mimetype


def analysis_do(analysis_id):
    return _script_export(analysis_id, _stata_script, ".do", "text/x-stata")


def analysis_r(analysis_id):
    return _script_export(analysis_id, _r_script, ".R", "text/x-r")


def analysis_py(analysis_id):
    return _script_export(analysis_id, _python_script, ".py", "text/x-python")


def analysis_zip(analysis_id):
    """Complete replication package: data + Stata/R/Python scripts + codebook
    + README. Ready for OSF or a journal data-availability policy."""
    analysis, study, params = _analysis_context(analysis_id)
    df = responses_dataframe(study)

    codebook = pd.DataFrame(
        [{"variable": c["id"], "label": c.get("label", ""),
          "type": c.get("kind", "")} for c in dataframe_columns(study)])

    readme = (
        f"# Replication package — {analysis.get('title', analysis['kind'])}\n\n"
        f"Study: {study.get('title', '')}\n"
        f"Research question: {study.get('research_question', '—')}\n"
        f"Analysis kind: {analysis['kind']}\n"
        f"Generated by NBU Research Agent on {analysis.get('created_at', '')}.\n\n"
        f"## Contents\n\n"
        f"- `{DATA_FILE}` — the analysis dataset ({len(df)} rows)\n"
        f"- `analysis.do` — Stata\n"
        f"- `analysis.R` — R\n"
        f"- `analysis.py` — Python (pandas/scipy/statsmodels)\n"
        f"- `codebook.csv` — variable names, labels, and types\n"
        f"- `results.json` — the results as computed by the platform\n\n"
        f"All three scripts reproduce the same analysis on `{DATA_FILE}`.\n")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(DATA_FILE, df.to_csv(index=False))
        z.writestr("analysis.do", _stata_script(analysis, study, params))
        z.writestr("analysis.R", _r_script(analysis, study, params))
        z.writestr("analysis.py", _python_script(analysis, study, params))
        z.writestr("codebook.csv", codebook.to_csv(index=False))
        z.writestr("results.json", json.dumps(analysis.get("results") or {}, indent=2))
        z.writestr("README.md", readme)
    name = f"replication-{slugify(analysis.get('title') or analysis['kind'])}.zip"
    return buf.getvalue(), name, "application/zip"
