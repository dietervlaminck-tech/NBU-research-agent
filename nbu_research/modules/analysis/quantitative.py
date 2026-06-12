"""Quantitative analysis of survey studies.

Builds a pandas DataFrame from survey responses (per the answers spec in
docs/INTERFACES.md) and runs classic statistics on it. Every analysis function
has the signature fn(study, params) -> JSON-serializable results dict.
"""
import json
import math
import re

import numpy as np
import pandas as pd
from scipy import stats as sps

from ... import db, llm
from ...config import DEFAULT_PIPELINE_MODEL

NUMERIC_TYPES = ("likert", "numeric")
CHOICE_TYPES = ("multiple_choice", "dropdown")


# --- DataFrame construction ---------------------------------------------------

def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return s or "x"


def is_dataset(target):
    """Analysis targets are polymorphic: a study row always carries
    `study_type`; a dataset row never does. This lets the dataframe builders
    serve both without touching the individual stat functions."""
    return "study_type" not in target


def dataframe_columns(target):
    """Flattened column spec: list of {"id", "label", "kind"} (numeric|categorical).

    For a survey study, derived from the questions. For a dataset, the stored
    column spec is returned verbatim.
    """
    if is_dataset(target):
        return target.get("columns") or []
    study = target
    cols = []
    for q in study.get("config", {}).get("questions", []):
        qid, qtype, qtext = q.get("id"), q.get("type"), q.get("text", "")
        if qtype in NUMERIC_TYPES:
            cols.append({"id": qid, "label": qtext, "kind": "numeric"})
        elif qtype == "matrix":
            for row in q.get("rows", []):
                cols.append({
                    "id": f"{qid}_{_slug(row)}",
                    "label": f"{qtext} — {row}",
                    "kind": "numeric",
                })
        elif qtype == "checkbox":
            for opt in q.get("options", []):
                cols.append({
                    "id": f"{qid}_{_slug(opt)}",
                    "label": f"{qtext}: {opt}",
                    "kind": "numeric",
                })
        elif qtype in CHOICE_TYPES or qtype == "open":
            cols.append({"id": qid, "label": qtext, "kind": "categorical"})
    return cols


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _dataset_dataframe(dataset):
    """Read a dataset's stored CSV into a DataFrame, coercing numeric columns
    per its column spec so the stat functions see the same dtypes as surveys."""
    import io
    text = dataset.get("data_csv") or ""
    cols = dataset.get("columns") or []
    if not text.strip():
        return pd.DataFrame(columns=[c["id"] for c in cols])
    df = pd.read_csv(io.StringIO(text))
    for c in cols:
        if c.get("kind") == "numeric" and c["id"] in df.columns:
            df[c["id"]] = pd.to_numeric(df[c["id"]], errors="coerce")
        elif c["id"] in df.columns:
            df[c["id"]] = df[c["id"]].where(df[c["id"]].notna(), None)
    keep = [c["id"] for c in cols if c["id"] in df.columns]
    return df[keep] if keep else df


def responses_dataframe(target):
    """One analysis-ready DataFrame (rows = observations, columns per
    dataframe_columns). Serves surveys and datasets alike.

    Survey cells: likert/numeric/matrix -> float (NaN when missing); checkbox
    option columns -> 0.0/1.0 (NaN when skipped); choice/open -> str.
    """
    if is_dataset(target):
        return _dataset_dataframe(target)
    study = target
    questions = study.get("config", {}).get("questions", [])
    responses = [r for r in db.query("survey_responses", "study_id = ?",
                                     (study["id"],), order="started_at ASC")
                 if r.get("answers")]
    rows, index = [], []
    for resp in responses:
        answers = resp.get("answers") or {}
        row = {}
        for q in questions:
            qid, qtype = q.get("id"), q.get("type")
            value = answers.get(qid)
            if qtype in NUMERIC_TYPES:
                row[qid] = _to_float(value)
            elif qtype == "matrix":
                cells = value if isinstance(value, dict) else {}
                for r_text in q.get("rows", []):
                    row[f"{qid}_{_slug(r_text)}"] = _to_float(cells.get(r_text))
            elif qtype == "checkbox":
                checked = value if isinstance(value, list) else None
                for opt in q.get("options", []):
                    col = f"{qid}_{_slug(opt)}"
                    row[col] = np.nan if checked is None else float(opt in checked)
            elif qtype in CHOICE_TYPES or qtype == "open":
                row[qid] = None if value is None else str(value)
        rows.append(row)
        index.append(resp["id"])
    col_ids = [c["id"] for c in dataframe_columns(study)]
    return pd.DataFrame(rows, index=index, columns=col_ids)


def _split_columns(study):
    cols = dataframe_columns(study)
    numeric = [c["id"] for c in cols if c["kind"] == "numeric"]
    categorical = [c["id"] for c in cols if c["kind"] == "categorical"]
    return numeric, categorical


# --- JSON sanitization --------------------------------------------------------

def _py(obj, ndigits=4):
    """Recursively convert numpy/pandas scalars to plain Python; round floats;
    map NaN/inf to None so results are JSON-serializable."""
    if isinstance(obj, dict):
        return {str(k): _py(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_py(v, ndigits) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, ndigits)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# --- Analysis kinds -----------------------------------------------------------

def descriptives(study, params):
    df = responses_dataframe(study)
    numeric, categorical = _split_columns(study)
    out_num = {}
    for col in numeric:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        out_num[col] = {
            "n": int(s.size),
            "mean": s.mean() if s.size else None,
            "sd": s.std(ddof=1) if s.size > 1 else None,
            "min": s.min() if s.size else None,
            "max": s.max() if s.size else None,
            "median": s.median() if s.size else None,
        }
    out_cat = {}
    for col in categorical:
        s = df[col].dropna()
        counts = s.value_counts()
        out_cat[col] = [
            {"value": str(v), "count": int(c),
             "pct": 100.0 * c / s.size if s.size else None}
            for v, c in counts.items()
        ]
    return _py({"n_responses": int(len(df)), "numeric": out_num, "categorical": out_cat})


def _cronbach_alpha(item_df):
    k = item_df.shape[1]
    variances = item_df.var(axis=0, ddof=1)
    total_var = item_df.sum(axis=1).var(ddof=1)
    if k < 2 or total_var == 0:
        return None
    return (k / (k - 1)) * (1 - variances.sum() / total_var)


def reliability(study, params):
    items = params.get("items") or []
    if len(items) < 2:
        raise ValueError("Reliability analysis needs at least 2 items.")
    df = responses_dataframe(study)
    missing = [i for i in items if i not in df.columns]
    if missing:
        raise ValueError(f"Unknown items: {', '.join(missing)}")
    data = df[items].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < 3:
        raise ValueError("Not enough complete responses for reliability analysis.")
    alpha = _cronbach_alpha(data)
    item_stats = []
    for item in items:
        rest = data.drop(columns=[item]).sum(axis=1)
        r_it = data[item].corr(rest)
        aid = _cronbach_alpha(data.drop(columns=[item])) if len(items) > 2 else None
        item_stats.append({
            "item": item,
            "mean": data[item].mean(),
            "sd": data[item].std(ddof=1),
            "item_total_r": r_it,
            "alpha_if_deleted": aid,
        })
    return _py({"alpha": alpha, "n_items": len(items), "n": int(len(data)),
                "items": item_stats})


def ttest(study, params):
    dv, group_col = params.get("dv"), params.get("group_col")
    if not dv or not group_col:
        raise ValueError("t-test needs a dependent variable and a grouping variable.")
    df = responses_dataframe(study)
    data = df[[dv, group_col]].copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    levels = sorted(data[group_col].astype(str).unique())
    if len(levels) != 2:
        raise ValueError(
            f"Independent-samples t-test requires exactly 2 groups; "
            f"'{group_col}' has {len(levels)} ({', '.join(map(str, levels))}). "
            "Use one-way ANOVA for more than 2 groups."
        )
    g1 = data.loc[data[group_col].astype(str) == levels[0], dv]
    g2 = data.loc[data[group_col].astype(str) == levels[1], dv]
    if len(g1) < 2 or len(g2) < 2:
        raise ValueError("Each group needs at least 2 observations.")
    t, p = sps.ttest_ind(g1, g2, equal_var=True)
    n1, n2 = len(g1), len(g2)
    pooled_sd = math.sqrt(((n1 - 1) * g1.var(ddof=1) + (n2 - 1) * g2.var(ddof=1))
                          / (n1 + n2 - 2))
    d = (g1.mean() - g2.mean()) / pooled_sd if pooled_sd > 0 else None
    return _py({
        "dv": dv, "group_col": group_col,
        "groups": [
            {"group": levels[0], "n": n1, "mean": g1.mean(), "sd": g1.std(ddof=1)},
            {"group": levels[1], "n": n2, "mean": g2.mean(), "sd": g2.std(ddof=1)},
        ],
        "t": t, "df": n1 + n2 - 2, "p": p, "cohens_d": d,
    })


def anova(study, params):
    dv, factor = params.get("dv"), params.get("factor")
    if not dv or not factor:
        raise ValueError("ANOVA needs a dependent variable and a factor.")
    df = responses_dataframe(study)
    data = df[[dv, factor]].copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    data[factor] = data[factor].astype(str)
    levels = sorted(data[factor].unique())
    groups = [data.loc[data[factor] == lv, dv] for lv in levels]
    if len(levels) < 2:
        raise ValueError(f"One-way ANOVA needs at least 2 groups; '{factor}' has {len(levels)}.")
    if any(len(g) < 2 for g in groups):
        raise ValueError("Each group needs at least 2 observations.")
    f_stat, p = sps.f_oneway(*groups)
    grand_mean = data[dv].mean()
    ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in groups)
    ss_total = ((data[dv] - grand_mean) ** 2).sum()
    eta_sq = ss_between / ss_total if ss_total > 0 else None
    return _py({
        "dv": dv, "factor": factor,
        "groups": [{"group": lv, "n": int(len(g)), "mean": g.mean(), "sd": g.std(ddof=1)}
                   for lv, g in zip(levels, groups)],
        "F": f_stat, "df_between": len(levels) - 1,
        "df_within": int(len(data)) - len(levels), "p": p, "eta_squared": eta_sq,
    })


def correlation(study, params):
    items = params.get("items") or []
    if len(items) < 2:
        raise ValueError("Correlation analysis needs at least 2 numeric items.")
    df = responses_dataframe(study)
    data = df[items].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < 3:
        raise ValueError("Not enough complete responses for correlations.")
    k = len(items)
    r_mat = [[None] * k for _ in range(k)]
    p_mat = [[None] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            if i == j:
                r_mat[i][j], p_mat[i][j] = 1.0, 0.0
            elif j < i:
                r_mat[i][j], p_mat[i][j] = r_mat[j][i], p_mat[j][i]
            else:
                r, p = sps.pearsonr(data[items[i]], data[items[j]])
                r_mat[i][j], p_mat[i][j] = r, p
    return _py({"items": items, "n": int(len(data)), "r": r_mat, "p": p_mat})


def regression(study, params):
    import statsmodels.api as sm
    dv, ivs = params.get("dv"), params.get("ivs") or []
    if not dv or not ivs:
        raise ValueError("Regression needs a dependent variable and at least 1 predictor.")
    if dv in ivs:
        raise ValueError("The dependent variable cannot also be a predictor.")
    df = responses_dataframe(study)
    data = df[[dv] + ivs].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < len(ivs) + 2:
        raise ValueError("Not enough complete responses for this regression model.")
    X = sm.add_constant(data[ivs].astype(float))
    model = sm.OLS(data[dv].astype(float), X).fit()
    coefs = []
    for term in model.params.index:
        coefs.append({
            "term": "Intercept" if term == "const" else term,
            "coef": model.params[term], "se": model.bse[term],
            "t": model.tvalues[term], "p": model.pvalues[term],
        })
    return _py({
        "dv": dv, "ivs": ivs, "n": int(model.nobs),
        "coefficients": coefs,
        "r_squared": model.rsquared, "adj_r_squared": model.rsquared_adj,
        "f_stat": model.fvalue, "f_p": model.f_pvalue,
        "df_model": model.df_model, "df_resid": model.df_resid,
    })


def crosstab(study, params):
    var1, var2 = params.get("var1"), params.get("var2")
    if not var1 or not var2 or var1 == var2:
        raise ValueError("Crosstab needs two different categorical variables.")
    df = responses_dataframe(study)
    data = df[[var1, var2]].dropna().astype(str)
    if len(data) < 2:
        raise ValueError("Not enough complete responses for a crosstab.")
    table = pd.crosstab(data[var1], data[var2])
    if table.shape[0] < 2 or table.shape[1] < 2:
        raise ValueError("Both variables need at least 2 observed categories.")
    chi2, p, dof, _ = sps.chi2_contingency(table)
    n = int(table.values.sum())
    cramers_v = math.sqrt(chi2 / (n * (min(table.shape) - 1)))
    return _py({
        "var1": var1, "var2": var2, "n": n,
        "row_labels": [str(x) for x in table.index],
        "col_labels": [str(x) for x in table.columns],
        "counts": table.values.tolist(),
        "chi2": chi2, "df": int(dof), "p": p, "cramers_v": cramers_v,
    })


# --- v0.2 analysis kinds --------------------------------------------------------

def _patch_factor_analyzer():
    """Compat shim: factor_analyzer 0.5.x calls sklearn's check_array with the
    `force_all_finite` kwarg that scikit-learn >= 1.6 renamed to
    `ensure_all_finite`. Wrap the reference factor_analyzer holds so EFA works
    on both sides of the rename. Idempotent."""
    import inspect
    import factor_analyzer.factor_analyzer as fa_mod
    original = fa_mod.check_array
    if getattr(original, "_nbu_patched", False):
        return
    if "force_all_finite" in inspect.signature(original).parameters:
        return  # old sklearn: nothing to fix

    def check_array_compat(*args, **kwargs):
        if "force_all_finite" in kwargs:
            kwargs["ensure_all_finite"] = kwargs.pop("force_all_finite")
        return original(*args, **kwargs)

    check_array_compat._nbu_patched = True
    fa_mod.check_array = check_array_compat


def efa(study, params):
    """Exploratory factor analysis (factor_analyzer, varimax rotation)."""
    from factor_analyzer import (FactorAnalyzer, calculate_kmo,
                                 calculate_bartlett_sphericity)
    _patch_factor_analyzer()
    items = params.get("items") or []
    if len(items) < 3:
        raise ValueError(f"EFA needs at least 3 items; got {len(items)}.")
    df = responses_dataframe(study)
    missing = [i for i in items if i not in df.columns]
    if missing:
        raise ValueError(f"Unknown items: {', '.join(missing)}")
    data = df[items].apply(pd.to_numeric, errors="coerce").dropna()
    k = len(items)
    need = max(2 * k, 10)
    if len(data) < need:
        raise ValueError(
            f"EFA on {k} items needs at least {need} complete responses; "
            f"got {len(data)}."
        )
    if (data.std(ddof=1) == 0).any():
        flat = [c for c in items if data[c].std(ddof=1) == 0]
        raise ValueError(f"Items without variance cannot be factored: {', '.join(flat)}")

    kmo_per_item, kmo_total = calculate_kmo(data)
    bartlett_chi2, bartlett_p = calculate_bartlett_sphericity(data)
    bartlett_df = k * (k - 1) // 2

    base = FactorAnalyzer(n_factors=k, rotation=None)
    base.fit(data)
    eigenvalues, _ = base.get_eigenvalues()

    n_factors = params.get("n_factors", "auto")
    if n_factors in (None, "", "auto"):
        nf = int((eigenvalues > 1).sum())
        nf = min(max(nf, 1), k - 1)
        method = "auto (Kaiser criterion, eigenvalue > 1)"
    else:
        try:
            nf = int(n_factors)
        except (TypeError, ValueError):
            raise ValueError(f"n_factors must be an integer or 'auto'; got {n_factors!r}.")
        if not 1 <= nf <= k - 1:
            raise ValueError(
                f"n_factors must be between 1 and {k - 1} for {k} items; got {nf}."
            )
        method = "specified"

    fa = FactorAnalyzer(n_factors=nf, rotation="varimax")
    fa.fit(data)
    loadings = fa.loadings_
    communalities = fa.get_communalities()
    ss_loadings, prop_var, cum_var = fa.get_factor_variance()

    return _py({
        "items": items, "n": int(len(data)),
        "kmo": float(kmo_total),
        "kmo_per_item": {item: float(v) for item, v in zip(items, kmo_per_item)},
        "bartlett_chi2": float(bartlett_chi2),
        "bartlett_df": bartlett_df,
        "bartlett_p": float(bartlett_p),
        "eigenvalues": list(eigenvalues),
        "n_factors": nf,
        "n_factors_method": method,
        "loadings": [{"item": item, "values": list(loadings[i])}
                     for i, item in enumerate(items)],
        "communalities": [{"item": item, "communality": float(communalities[i])}
                          for i, item in enumerate(items)],
        "variance": [{"factor": f"F{j + 1}",
                      "ss_loadings": float(ss_loadings[j]),
                      "pct_variance": 100.0 * float(prop_var[j]),
                      "cumulative_pct": 100.0 * float(cum_var[j])}
                     for j in range(nf)],
    })


def manova(study, params):
    from statsmodels.multivariate.manova import MANOVA
    dvs, factor = params.get("dvs") or [], params.get("factor")
    if len(dvs) < 2:
        raise ValueError(f"MANOVA needs at least 2 dependent variables; got {len(dvs)}.")
    if not factor:
        raise ValueError("MANOVA needs a grouping factor.")
    if factor in dvs:
        raise ValueError("The factor cannot also be a dependent variable.")
    df = responses_dataframe(study)
    missing = [c for c in dvs + [factor] if c not in df.columns]
    if missing:
        raise ValueError(f"Unknown columns: {', '.join(missing)}")
    data = df[dvs + [factor]].copy()
    for dv in dvs:
        data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    data[factor] = data[factor].astype(str)
    levels = sorted(data[factor].unique())
    if len(levels) < 2:
        raise ValueError(f"MANOVA needs at least 2 groups; '{factor}' has {len(levels)}.")
    if any((data[factor] == lv).sum() <= len(dvs) for lv in levels):
        raise ValueError(
            f"Each group needs more observations than DVs ({len(dvs)})."
        )
    formula = " + ".join(dvs) + f" ~ C({factor})"
    mv = MANOVA.from_formula(formula, data=data)
    res = mv.mv_test()
    term = next(key for key in res.results if key != "Intercept")
    stat_df = res.results[term]["stat"]
    tests = []
    for test_name, row in stat_df.iterrows():
        tests.append({
            "test": str(test_name),
            "value": row["Value"],
            "F": row["F Value"],
            "df_num": row["Num DF"],
            "df_den": row["Den DF"],
            "p": row["Pr > F"],
        })
    groups = []
    for lv in levels:
        sub = data.loc[data[factor] == lv, dvs]
        groups.append({"group": lv, "n": int(len(sub)),
                       "means": {dv: sub[dv].mean() for dv in dvs}})
    return _py({"dvs": dvs, "factor": factor, "n": int(len(data)),
                "groups": groups, "tests": tests})


def mannwhitney(study, params):
    dv, group_col = params.get("dv"), params.get("group_col")
    if not dv or not group_col:
        raise ValueError("Mann-Whitney U needs a dependent variable and a grouping variable.")
    df = responses_dataframe(study)
    data = df[[dv, group_col]].copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    levels = sorted(data[group_col].astype(str).unique())
    if len(levels) != 2:
        raise ValueError(
            f"Mann-Whitney U requires exactly 2 groups; "
            f"'{group_col}' has {len(levels)} ({', '.join(map(str, levels))}). "
            "Use Kruskal-Wallis for more than 2 groups."
        )
    g1 = data.loc[data[group_col].astype(str) == levels[0], dv]
    g2 = data.loc[data[group_col].astype(str) == levels[1], dv]
    if len(g1) < 2 or len(g2) < 2:
        raise ValueError("Each group needs at least 2 observations.")
    u_stat, p = sps.mannwhitneyu(g1, g2, alternative="two-sided")
    n1, n2 = len(g1), len(g2)
    rank_biserial = 1 - 2 * float(u_stat) / (n1 * n2)
    return _py({
        "dv": dv, "group_col": group_col,
        "groups": [
            {"group": levels[0], "n": n1, "median": g1.median()},
            {"group": levels[1], "n": n2, "median": g2.median()},
        ],
        "U": float(u_stat), "p": p, "rank_biserial": rank_biserial,
    })


def kruskal(study, params):
    dv, factor = params.get("dv"), params.get("factor")
    if not dv or not factor:
        raise ValueError("Kruskal-Wallis needs a dependent variable and a factor.")
    df = responses_dataframe(study)
    data = df[[dv, factor]].copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    data[factor] = data[factor].astype(str)
    levels = sorted(data[factor].unique())
    if len(levels) < 2:
        raise ValueError(f"Kruskal-Wallis needs at least 2 groups; '{factor}' has {len(levels)}.")
    groups = [data.loc[data[factor] == lv, dv] for lv in levels]
    if any(len(g) < 2 for g in groups):
        raise ValueError("Each group needs at least 2 observations.")
    h_stat, p = sps.kruskal(*groups)
    n = int(len(data))
    epsilon_sq = float(h_stat) * (n + 1) / (n ** 2 - 1) if n > 1 else None
    return _py({
        "dv": dv, "factor": factor, "n": n,
        "groups": [{"group": lv, "n": int(len(g)), "median": g.median()}
                   for lv, g in zip(levels, groups)],
        "H": float(h_stat), "df": len(levels) - 1, "p": p,
        "epsilon_squared": epsilon_sq,
    })


def wilcoxon(study, params):
    item_a, item_b = params.get("item_a"), params.get("item_b")
    if not item_a or not item_b or item_a == item_b:
        raise ValueError("Wilcoxon signed-rank test needs two different numeric items.")
    df = responses_dataframe(study)
    missing = [c for c in (item_a, item_b) if c not in df.columns]
    if missing:
        raise ValueError(f"Unknown items: {', '.join(missing)}")
    pair = pd.DataFrame({
        "a": pd.to_numeric(df[item_a], errors="coerce"),
        "b": pd.to_numeric(df[item_b], errors="coerce"),
    }).dropna()
    n_pairs = int(len(pair))
    if n_pairs < 5:
        raise ValueError(f"Wilcoxon needs at least 5 complete pairs; got {n_pairs}.")
    diff = pair["a"] - pair["b"]
    nonzero = diff[diff != 0]
    if nonzero.empty:
        raise ValueError("The two items are identical in every pair — nothing to test.")
    w_stat, p = sps.wilcoxon(pair["a"], pair["b"])
    ranks = sps.rankdata(nonzero.abs())
    w_plus = float(ranks[(nonzero > 0).to_numpy()].sum())
    w_minus = float(ranks[(nonzero < 0).to_numpy()].sum())
    rank_biserial = (w_plus - w_minus) / (w_plus + w_minus)
    return _py({
        "item_a": item_a, "item_b": item_b, "n_pairs": n_pairs,
        "median_a": pair["a"].median(), "median_b": pair["b"].median(),
        "W": float(w_stat), "p": p, "rank_biserial": rank_biserial,
    })


# --- Effect-size report builder -------------------------------------------------

_MAGNITUDE_CUTS = {
    # family: thresholds for negligible / small / medium (else large)
    "d": (0.2, 0.5, 0.8),
    "r": (0.1, 0.3, 0.5),
    "eta": (0.01, 0.06, 0.14),
}


def _effect_magnitude(value, family):
    """Conventional verbal label for an effect size (Cohen's benchmarks)."""
    if value is None:
        return None
    v = abs(float(value))
    for cut, label in zip(_MAGNITUDE_CUTS[family], ("negligible", "small", "medium")):
        if v < cut:
            return label
    return "large"


def effect_sizes(study, params):
    """Report builder: aggregate effect sizes from the target's completed analyses.

    Reads the exact result keys produced by the analysis kinds in this module
    (ttest: cohens_d; anova: eta_squared; mannwhitney/wilcoxon: rank_biserial;
    kruskal: epsilon_squared; correlation: r matrix; regression: r_squared /
    adj_r_squared; crosstab: cramers_v).
    """
    target = study  # polymorphic, same as every other kind
    ref_col = "dataset_id" if is_dataset(target) else "study_id"
    past = db.query("analyses", f"{ref_col} = ? AND status = ?",
                    (target["id"], "done"), order="created_at ASC")
    past = [a for a in past if a.get("kind") != "effect_sizes"]
    if not past:
        raise ValueError(
            "No completed analyses found for this target — run some analyses "
            "before building an effect-size report."
        )
    effects, notes = [], []
    for a in past:
        res = a.get("results") or {}
        kind, title = a.get("kind"), a.get("title") or a.get("kind")
        found = []
        if kind == "ttest" and res.get("cohens_d") is not None:
            found.append(("Cohen's d", res["cohens_d"], "d"))
        elif kind == "anova" and res.get("eta_squared") is not None:
            found.append(("eta-squared", res["eta_squared"], "eta"))
        elif kind == "mannwhitney" and res.get("rank_biserial") is not None:
            found.append(("rank-biserial r", res["rank_biserial"], "r"))
        elif kind == "wilcoxon" and res.get("rank_biserial") is not None:
            found.append(("matched rank-biserial r", res["rank_biserial"], "r"))
        elif kind == "kruskal" and res.get("epsilon_squared") is not None:
            found.append(("epsilon-squared", res["epsilon_squared"], "eta"))
        elif kind == "correlation" and res.get("r"):
            items = res.get("items") or []
            r_mat = res.get("r") or []
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    if i < len(r_mat) and j < len(r_mat[i]) and r_mat[i][j] is not None:
                        found.append((f"r({items[i]}, {items[j]})", r_mat[i][j], "r"))
        elif kind == "regression":
            if res.get("r_squared") is not None:
                found.append(("R-squared", res["r_squared"], "eta"))
            if res.get("adj_r_squared") is not None:
                found.append(("Adjusted R-squared", res["adj_r_squared"], "eta"))
        elif kind == "crosstab" and res.get("cramers_v") is not None:
            found.append(("Cramer's V", res["cramers_v"], "r"))
        if found:
            for effect, value, family in found:
                effects.append({
                    "analysis_title": title, "kind": kind, "effect": effect,
                    "value": value, "magnitude": _effect_magnitude(value, family),
                })
        else:
            notes.append(f"{title} ({kind}) carried no effect size.")
    return _py({"n_analyses": len(past), "effects": effects, "notes": notes})


ANALYSIS_KINDS = {
    "descriptives": (descriptives, "Descriptive statistics"),
    "reliability": (reliability, "Reliability (Cronbach's alpha)"),
    "ttest": (ttest, "Independent-samples t-test"),
    "anova": (anova, "One-way ANOVA"),
    "correlation": (correlation, "Pearson correlations"),
    "regression": (regression, "OLS regression"),
    "crosstab": (crosstab, "Crosstab (chi-square)"),
    "efa": (efa, "Exploratory factor analysis"),
    "manova": (manova, "One-way MANOVA"),
    "mannwhitney": (mannwhitney, "Mann-Whitney U test"),
    "kruskal": (kruskal, "Kruskal-Wallis H test"),
    "wilcoxon": (wilcoxon, "Wilcoxon signed-rank test (paired)"),
    "effect_sizes": (effect_sizes, "Effect-size report"),
}


# --- Form-field specs for the v0.2 kinds (consumed by the integration pass) -----
#
# Each entry describes the form for one analysis kind. Field kinds:
#   numeric_select        one numeric column      -> params[name] = str
#   numeric_multiselect   many numeric columns    -> params[name] = [str, ...]
#   categorical_select    one categorical column  -> params[name] = str
#   text                  free text input         -> params[name] = str
# "params_from_form" tells the generic form parser how to build the params
# dict: multiselect_fields -> request.form.getlist(name); single_fields ->
# request.form.get(name) (omit when blank); int_fields -> additionally parse
# the value as int when present and non-blank (blank n_factors stays absent,
# which the efa function treats as "auto").
PARAM_SPECS = {
    "efa": {
        "fields": [
            {"name": "items", "kind": "numeric_multiselect",
             "label": "Items (numeric, at least 3)", "required": True},
            {"name": "n_factors", "kind": "text",
             "label": "Number of factors (blank = auto)", "required": False},
        ],
        "params_from_form": {"multiselect_fields": ["items"],
                             "single_fields": ["n_factors"],
                             "int_fields": ["n_factors"]},
    },
    "manova": {
        "fields": [
            {"name": "dvs", "kind": "numeric_multiselect",
             "label": "Dependent variables (at least 2)", "required": True},
            {"name": "factor", "kind": "categorical_select",
             "label": "Grouping factor", "required": True},
        ],
        "params_from_form": {"multiselect_fields": ["dvs"],
                             "single_fields": ["factor"],
                             "int_fields": []},
    },
    "mannwhitney": {
        "fields": [
            {"name": "dv", "kind": "numeric_select",
             "label": "Dependent variable", "required": True},
            {"name": "group_col", "kind": "categorical_select",
             "label": "Grouping variable (2 groups)", "required": True},
        ],
        "params_from_form": {"multiselect_fields": [],
                             "single_fields": ["dv", "group_col"],
                             "int_fields": []},
    },
    "kruskal": {
        "fields": [
            {"name": "dv", "kind": "numeric_select",
             "label": "Dependent variable", "required": True},
            {"name": "factor", "kind": "categorical_select",
             "label": "Grouping factor", "required": True},
        ],
        "params_from_form": {"multiselect_fields": [],
                             "single_fields": ["dv", "factor"],
                             "int_fields": []},
    },
    "wilcoxon": {
        "fields": [
            {"name": "item_a", "kind": "numeric_select",
             "label": "First item", "required": True},
            {"name": "item_b", "kind": "numeric_select",
             "label": "Second item", "required": True},
        ],
        "params_from_form": {"multiselect_fields": [],
                             "single_fields": ["item_a", "item_b"],
                             "int_fields": []},
    },
    "effect_sizes": {
        "fields": [],
        "params_from_form": {"multiselect_fields": [],
                             "single_fields": [],
                             "int_fields": []},
    },
}


# --- Interpretation + persistence ----------------------------------------------

def target_title(target):
    """Display name of a study or dataset analysis target."""
    return target.get("title") or target.get("name") or target.get("id", "")


def _interpret(kind, target, results):
    """Short APA-style interpretation via the LLM; None when unavailable."""
    try:
        system = (
            "You are a quantitative methodologist writing for an academic audience. "
            "Given the statistical results of one analysis, write a single short "
            "APA-style interpretation paragraph (4-6 sentences). Report statistics "
            "in APA format, comment on effect sizes and significance, and relate "
            "the finding to the research question when one is given. Plain prose, "
            "no headings, no bullet points."
        )
        context = target.get("research_question") or target.get("description") or "(not specified)"
        prompt = (
            f"Analysis kind: {ANALYSIS_KINDS[kind][1]}\n"
            f"Data source: {target_title(target)}\n"
            f"Research question / context: {context}\n\n"
            f"Results (JSON):\n{json.dumps(results, indent=2)}"
        )
        return llm.complete(system, prompt, model=DEFAULT_PIPELINE_MODEL, max_tokens=1500)
    except Exception:
        return None


def run_analysis(target, kind, params):
    """Run one quantitative analysis synchronously and persist it. `target` is a
    survey study row or a dataset row.

    Returns the new analyses row id. Raises ValueError for bad params.
    """
    if kind not in ANALYSIS_KINDS:
        raise ValueError(f"Unknown analysis kind: {kind}")
    fn, label = ANALYSIS_KINDS[kind]
    results = fn(target, params)
    results["interpretation"] = _interpret(kind, target, results)
    ref = {"dataset_id": target["id"]} if is_dataset(target) else {"study_id": target["id"]}
    return db.insert("analyses", {
        **ref,
        "kind": kind,
        "project_id": target.get("project_id"),
        "title": f"{label} — {target_title(target)}",
        "params": params,
        "results": results,
        "status": "done",
        "completed_at": db.now(),
    })


# --- Result presentation (used by the result template) -------------------------

def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def result_tables(kind, results):
    """Render results as a list of {"title", "columns", "rows"} tables."""
    tables = []

    def add(title, columns, rows):
        tables.append({"title": title, "columns": columns,
                       "rows": [[_fmt(c) for c in r] for r in rows]})

    if kind == "descriptives":
        add("Numeric variables", ["Variable", "n", "Mean", "SD", "Min", "Max", "Median"],
            [[col, s["n"], s["mean"], s["sd"], s["min"], s["max"], s["median"]]
             for col, s in results.get("numeric", {}).items()])
        cat_rows = []
        for col, freqs in results.get("categorical", {}).items():
            for f in freqs:
                cat_rows.append([col, f["value"], f["count"],
                                 None if f["pct"] is None else round(f["pct"], 1)])
        add("Categorical frequencies", ["Variable", "Value", "Count", "%"], cat_rows)

    elif kind == "reliability":
        add("Scale", ["Cronbach's alpha", "Items", "n"],
            [[results.get("alpha"), results.get("n_items"), results.get("n")]])
        add("Item statistics",
            ["Item", "Mean", "SD", "Item-total r", "Alpha if deleted"],
            [[i["item"], i["mean"], i["sd"], i["item_total_r"], i["alpha_if_deleted"]]
             for i in results.get("items", [])])

    elif kind == "ttest":
        add("Group statistics", ["Group", "n", "Mean", "SD"],
            [[g["group"], g["n"], g["mean"], g["sd"]] for g in results.get("groups", [])])
        add("Test", ["t", "df", "p", "Cohen's d"],
            [[results.get("t"), results.get("df"), results.get("p"),
              results.get("cohens_d")]])

    elif kind == "anova":
        add("Group statistics", ["Group", "n", "Mean", "SD"],
            [[g["group"], g["n"], g["mean"], g["sd"]] for g in results.get("groups", [])])
        add("Test", ["F", "df between", "df within", "p", "Eta squared"],
            [[results.get("F"), results.get("df_between"), results.get("df_within"),
              results.get("p"), results.get("eta_squared")]])

    elif kind == "correlation":
        items = results.get("items", [])
        add(f"Pearson r (n = {results.get('n')})", [""] + items,
            [[items[i]] + row for i, row in enumerate(results.get("r", []))])
        add("p-values", [""] + items,
            [[items[i]] + row for i, row in enumerate(results.get("p", []))])

    elif kind == "regression":
        add("Coefficients", ["Term", "B", "SE", "t", "p"],
            [[c["term"], c["coef"], c["se"], c["t"], c["p"]]
             for c in results.get("coefficients", [])])
        add("Model", ["R²", "Adj. R²", "F", "p (F)", "n"],
            [[results.get("r_squared"), results.get("adj_r_squared"),
              results.get("f_stat"), results.get("f_p"), results.get("n")]])

    elif kind == "crosstab":
        cols = results.get("col_labels", [])
        rows = []
        for label, counts in zip(results.get("row_labels", []), results.get("counts", [])):
            rows.append([label] + counts + [sum(counts)])
        col_totals = [sum(r[i + 1] for r in rows) for i in range(len(cols))]
        rows.append(["Total"] + col_totals + [results.get("n")])
        add(f"{results.get('var1')} × {results.get('var2')}",
            [""] + cols + ["Total"], rows)
        add("Test", ["Chi-square", "df", "p", "Cramér's V", "n"],
            [[results.get("chi2"), results.get("df"), results.get("p"),
              results.get("cramers_v"), results.get("n")]])

    elif kind == "efa":
        add("Sampling adequacy & sphericity",
            ["KMO", "Bartlett χ²", "df", "p", "n"],
            [[results.get("kmo"), results.get("bartlett_chi2"),
              results.get("bartlett_df"), results.get("bartlett_p"),
              results.get("n")]])
        add("Eigenvalues", ["Component", "Eigenvalue"],
            [[i + 1, ev] for i, ev in enumerate(results.get("eigenvalues", []))])
        nf = results.get("n_factors") or 0
        factor_cols = [f"F{j + 1}" for j in range(nf)]
        add(f"Factor loadings (varimax, {results.get('n_factors_method', '')})",
            ["Item"] + factor_cols,
            [[l["item"]] + list(l.get("values", [])) for l in results.get("loadings", [])])
        add("Communalities", ["Item", "Communality"],
            [[c["item"], c["communality"]] for c in results.get("communalities", [])])
        add("Variance explained",
            ["Factor", "SS loadings", "% variance", "Cumulative %"],
            [[v["factor"], v["ss_loadings"], v["pct_variance"], v["cumulative_pct"]]
             for v in results.get("variance", [])])

    elif kind == "manova":
        add("Multivariate tests", ["Test", "Value", "F", "df (num)", "df (den)", "p"],
            [[t["test"], t["value"], t["F"], t["df_num"], t["df_den"], t["p"]]
             for t in results.get("tests", [])])
        dvs = results.get("dvs", [])
        add("Group means", ["Group", "n"] + dvs,
            [[g["group"], g["n"]] + [g.get("means", {}).get(dv) for dv in dvs]
             for g in results.get("groups", [])])

    elif kind == "mannwhitney":
        add("Group statistics", ["Group", "n", "Median"],
            [[g["group"], g["n"], g["median"]] for g in results.get("groups", [])])
        add("Test", ["U", "p", "Rank-biserial r"],
            [[results.get("U"), results.get("p"), results.get("rank_biserial")]])

    elif kind == "kruskal":
        add("Group statistics", ["Group", "n", "Median"],
            [[g["group"], g["n"], g["median"]] for g in results.get("groups", [])])
        add("Test", ["H", "df", "p", "Epsilon-squared"],
            [[results.get("H"), results.get("df"), results.get("p"),
              results.get("epsilon_squared")]])

    elif kind == "wilcoxon":
        add("Item statistics", ["Item", "Median"],
            [[results.get("item_a"), results.get("median_a")],
             [results.get("item_b"), results.get("median_b")]])
        add("Test", ["W", "n pairs", "p", "Matched rank-biserial r"],
            [[results.get("W"), results.get("n_pairs"), results.get("p"),
              results.get("rank_biserial")]])

    elif kind == "effect_sizes":
        add("Effect sizes", ["Analysis", "Kind", "Effect", "Value", "Magnitude"],
            [[e["analysis_title"], e["kind"], e["effect"], e["value"], e["magnitude"]]
             for e in results.get("effects", [])])
        if results.get("notes"):
            add("Analyses without effect sizes", ["Note"],
                [[n] for n in results.get("notes", [])])

    return tables
