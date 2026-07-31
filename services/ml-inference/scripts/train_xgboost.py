#!/usr/bin/env python3
"""Offline supervised training for the one detector allowed to see ground
truth: pulls historical `features` + `raw_events.scenario_label` from
ClickHouse, labels each feature window anomalous if any raw event inside
its [window_start, window_end) span carried a non-empty scenario_label,
trains one binary XGBoost classifier per domain on the same feature vector
`app/features.py::to_vector` builds for live scoring, and saves each to
`app/models/artifacts/xgboost_{domain}.json`.

This is the *only* place `scenario_label` is allowed to influence the
system — a human/CI job, run offline, never the live consumer loop (see
docs/metrics.md). `ml-inference` picks the artifact up automatically
(`XGBoostDetector` checks the file at construction; call its `.reload()`,
or just restart the service, to pick up a freshly (re)trained model).

Model selection uses time-series cross-validation
(`sklearn.model_selection.TimeSeriesSplit`, expanding window), not a random
k-fold or a single train/test split: feature windows are chronologically
ordered and drift over time (see docs/roadmap.md's regime-change note), so
a random shuffle would leak future information into earlier folds' training
data - a real, easy-to-miss mistake for this kind of data, not a
theoretical one. The deployed model is trained on the CV's *last* fold's
training slice only (not refit on everything afterward): the held-out
metrics this script reports describe the exact artifact that ships, not a
"probably still this good after retraining on more data" claim.

A small hyperparameter grid is searched by mean held-out average precision
(AUCPR) across folds; the final model additionally uses early stopping
against its own held-out split to pick the boosting round count. An
isotonic calibrator is then fit on that same held-out split's predictions
(see app/calibration.py) and saved alongside the model as
`xgboost_{domain}_calibration.json` - `XGBoostDetector` applies it at score
time. Brier score before/after calibration is reported so "calibration was
added" is a measured claim, not just a checkbox.

Usage:
    python scripts/train_xgboost.py --min-rows 200
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.calibration import apply_calibration, brier_score, fit_isotonic_calibration
from app.config import settings
from app.features import feature_names

PARAM_GRID: list[dict] = [
    {"max_depth": max_depth, "eta": eta, "min_child_weight": min_child_weight}
    for max_depth in (3, 4, 5)
    for eta in (0.05, 0.1, 0.2)
    for min_child_weight in (1, 5)
]
MAX_BOOST_ROUNDS = 300
EARLY_STOPPING_ROUNDS = 15
N_CV_SPLITS = 3


def clickhouse_select(base_url: str, db: str, user: str, password: str, query: str) -> pd.DataFrame:
    resp = httpx.post(
        f"{base_url}/",
        params={"database": db, "query": f"{query} FORMAT JSONEachRow"},
        auth=(user, password),
        timeout=120,
    )
    resp.raise_for_status()
    rows = [json.loads(line) for line in resp.text.strip().splitlines() if line]
    return pd.DataFrame(rows)


def label_features(features_df: pd.DataFrame, events_df: pd.DataFrame) -> np.ndarray:
    """O(entities * windows * events-per-entity) interval membership check.
    Fine at demo scale (thousands of rows); at real production scale this
    would move to a ClickHouse ASOF JOIN or a range-partitioned merge.
    """
    labeled_events = events_df[events_df["scenario_label"] != ""].copy()
    labeled_events["ts_event_dt"] = pd.to_datetime(labeled_events["ts_event"])
    window_start = pd.to_datetime(features_df["window_start"])
    window_end = pd.to_datetime(features_df["window_end"])

    labels = np.zeros(len(features_df), dtype=int)
    for entity, group in features_df.groupby("entity_key"):
        ev = labeled_events[labeled_events["entity_key"] == entity]
        if ev.empty:
            continue
        ev_times = ev["ts_event_dt"].to_numpy()
        for pos, idx in enumerate(group.index):
            s, e = window_start[idx].to_datetime64(), window_end[idx].to_datetime64()
            hit = bool(((ev_times >= s) & (ev_times < e)).any())
            labels[features_df.index.get_loc(idx)] = int(hit)
    return labels


def build_vector(domain: str, row: pd.Series) -> list[float]:
    if domain == "market":
        return [
            row["zscore"], row.get("realized_vol") or 0.0, row.get("spread_bps") or 0.0,
            row.get("order_imbalance") or 0.0, row["throughput_eps"], row["latency_p99_ms"], row["error_rate"],
            row["velocity_count"],
        ]
    return [
        row["zscore"], np.log1p(max(row.get("mean_amount") or 0.0, 0.0)), row.get("decline_rate") or 0.0,
        float(row.get("distinct_accounts") or 0), row["throughput_eps"], row["latency_p99_ms"], row["error_rate"],
        row["velocity_count"],
    ]


def add_velocity_feature(df: pd.DataFrame, window_count: int) -> pd.DataFrame:
    """Rolling sum of `count` over the last `window_count` windows per
    entity - the offline mirror of app/detectors/velocity.py's
    RollingVelocity, computed here via pandas instead of a live per-entity
    deque. `df` must already be sorted chronologically (by window_end) for
    the per-entity groupby to preserve chronological order within each
    entity's rows - `.transform` keeps df's row order, it doesn't resort."""
    df = df.copy()
    df["velocity_count"] = df.groupby("entity_key")["count"].transform(lambda s: s.rolling(window=window_count, min_periods=1).sum())
    return df


def _train_one(params: dict, x_train, y_train, x_val, y_val) -> xgb.Booster:
    dtrain = xgb.DMatrix(x_train, label=y_train)
    dval = xgb.DMatrix(x_val, label=y_val)
    return xgb.train(
        {**params, "objective": "binary:logistic", "eval_metric": "aucpr"},
        dtrain,
        num_boost_round=MAX_BOOST_ROUNDS,
        evals=[(dval, "val")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=False,
    )


def select_best_hyperparams(x: np.ndarray, y: np.ndarray, param_grid: list[dict], n_splits: int = N_CV_SPLITS) -> tuple[dict, float]:
    """Time-series CV (expanding window) hyperparameter search: each fold
    only ever validates on data chronologically *after* what it trained on.
    Returns the params with the highest mean held-out AUCPR across folds
    (folds with zero positives in either split are skipped - AUCPR isn't
    meaningful there), plus that mean score."""
    splitter = TimeSeriesSplit(n_splits=n_splits)
    best_params, best_score = param_grid[0], -1.0
    for params in param_grid:
        fold_scores = []
        for train_idx, val_idx in splitter.split(x):
            if y[train_idx].sum() == 0 or y[val_idx].sum() == 0:
                continue
            booster = _train_one(params, x[train_idx], y[train_idx], x[val_idx], y[val_idx])
            dval = xgb.DMatrix(x[val_idx])
            preds = booster.predict(dval, iteration_range=(0, booster.best_iteration + 1))
            fold_scores.append(average_precision_score(y[val_idx], preds))
        if fold_scores:
            mean_score = float(np.mean(fold_scores))
            if mean_score > best_score:
                best_score, best_params = mean_score, params
    return best_params, best_score


def train_domain(domain: str, features_df: pd.DataFrame, labels: np.ndarray, min_rows: int) -> dict | None:
    mask = features_df["domain"] == domain
    # `labels` is positionally aligned with features_df's original row order;
    # attach it by that same original index before sorting chronologically,
    # so `y` stays aligned with `df` after the reorder (time-series CV below
    # depends on x/y actually being in window_end order, not just labeled as
    # if they were).
    y_by_index = pd.Series(labels[mask.to_numpy()], index=features_df[mask].index)
    df = features_df[mask].sort_values("window_end")
    df = add_velocity_feature(df, settings.velocity_window_count)
    y = y_by_index.loc[df.index].to_numpy()

    if len(df) < min_rows:
        print(f"[{domain}] only {len(df)} rows (< --min-rows={min_rows}), skipping")
        return None
    if y.sum() == 0:
        print(f"[{domain}] no positive (anomalous) examples in {len(df)} rows, skipping")
        return None

    x = np.array([build_vector(domain, row) for _, row in df.iterrows()], dtype=np.float32)

    best_params, cv_aucpr = select_best_hyperparams(x, y, PARAM_GRID)
    print(f"[{domain}] CV-selected params={best_params} mean_val_aucpr={cv_aucpr:.4f}")

    # Final split: the *last* TimeSeriesSplit fold - the deployed model is
    # trained on this train slice only and evaluated on this held-out slice;
    # nothing is refit on the held-out data afterward (see module docstring).
    train_idx, val_idx = list(TimeSeriesSplit(n_splits=N_CV_SPLITS).split(x))[-1]
    if y[train_idx].sum() == 0 or y[val_idx].sum() == 0:
        print(f"[{domain}] final held-out split has no positives in train or val, skipping")
        return None

    booster = _train_one(best_params, x[train_idx], y[train_idx], x[val_idx], y[val_idx])
    dval = xgb.DMatrix(x[val_idx], feature_names=feature_names(domain))
    raw_val_scores = booster.predict(dval, iteration_range=(0, booster.best_iteration + 1))
    y_val = y[val_idx]

    held_out_aucpr = float(average_precision_score(y_val, raw_val_scores))
    brier_before = brier_score(raw_val_scores, y_val)
    calibration = fit_isotonic_calibration(raw_val_scores, y_val)
    calibrated_val_scores = np.array([apply_calibration(s, calibration) for s in raw_val_scores])
    brier_after = brier_score(calibrated_val_scores, y_val)

    model_path = settings.xgboost_model_path.format(domain=domain)
    calibration_path = model_path.replace(".json", "_calibration.json")
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    booster.save_model(model_path)
    with open(calibration_path, "w") as f:
        json.dump(calibration, f)

    report = {
        "domain": domain,
        "train_rows": len(train_idx),
        "held_out_rows": len(val_idx),
        "held_out_positive_rows": int(y_val.sum()),
        "best_params": {**best_params, "best_iteration": int(booster.best_iteration)},
        "cv_mean_val_aucpr": cv_aucpr,
        "held_out_aucpr": held_out_aucpr,
        "brier_score_before_calibration": brier_before,
        "brier_score_after_calibration": brier_after,
    }
    print(
        f"[{domain}] trained on {len(train_idx)} rows ({int(y[train_idx].sum())} positive), "
        f"held out {len(val_idx)} rows ({int(y_val.sum())} positive) -> {model_path}\n"
        f"[{domain}] held_out_aucpr={held_out_aucpr:.4f} brier_before={brier_before:.4f} brier_after={brier_after:.4f}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    base_url = f"http://{settings.clickhouse_host}:{settings.clickhouse_http_port}"
    common = (base_url, settings.clickhouse_db, settings.clickhouse_user, settings.clickhouse_password)

    features_df = clickhouse_select(
        *common,
        "SELECT entity_key, domain, window_start, window_end, count, zscore, realized_vol, spread_bps, "
        "order_imbalance, mean_amount, decline_rate, distinct_accounts, throughput_eps, "
        "latency_p99_ms, error_rate FROM features",
    )
    events_df = clickhouse_select(*common, "SELECT entity_key, domain, ts_event, scenario_label FROM raw_events")

    if features_df.empty:
        print("no rows in risk.features yet - run the pipeline for a while first")
        return

    labels = label_features(features_df, events_df)
    print(f"loaded {len(features_df)} feature windows, {int(labels.sum())} labeled anomalous from {len(events_df)} raw events")

    reports = {}
    for domain in ("market", "payments"):
        report = train_domain(domain, features_df, labels, args.min_rows)
        if report:
            reports[domain] = report

    if reports:
        out_path = os.path.abspath(args.out)
        existing = {}
        if os.path.exists(out_path):
            with open(out_path) as f:
                existing = json.load(f)
        existing["xgboost_training"] = {"generated_at": datetime.now(timezone.utc).isoformat(), "by_domain": reports}
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(existing, f, indent=2, default=str)
        print(f"\nwrote training report -> {out_path}")


if __name__ == "__main__":
    main()
