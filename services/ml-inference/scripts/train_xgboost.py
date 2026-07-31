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

Usage:
    python scripts/train_xgboost.py --min-rows 200
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.features import feature_names


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
        ]
    return [
        row["zscore"], np.log1p(max(row.get("mean_amount") or 0.0, 0.0)), row.get("decline_rate") or 0.0,
        float(row.get("distinct_accounts") or 0), row["throughput_eps"], row["latency_p99_ms"], row["error_rate"],
    ]


def train_domain(domain: str, features_df: pd.DataFrame, labels: np.ndarray, min_rows: int) -> bool:
    mask = features_df["domain"] == domain
    df = features_df[mask]
    y = labels[mask.to_numpy()]
    if len(df) < min_rows:
        print(f"[{domain}] only {len(df)} rows (< --min-rows={min_rows}), skipping")
        return False
    if y.sum() == 0:
        print(f"[{domain}] no positive (anomalous) examples in {len(df)} rows, skipping")
        return False

    x = np.array([build_vector(domain, row) for _, row in df.iterrows()], dtype=np.float32)
    dtrain = xgb.DMatrix(x, label=y, feature_names=feature_names(domain))
    booster = xgb.train(
        {"objective": "binary:logistic", "max_depth": 4, "eta": 0.1, "eval_metric": "aucpr"},
        dtrain,
        num_boost_round=100,
    )

    path = settings.xgboost_model_path.format(domain=domain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    booster.save_model(path)
    print(f"[{domain}] trained on {len(df)} rows ({int(y.sum())} positive) -> {path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-rows", type=int, default=200)
    args = parser.parse_args()

    base_url = f"http://{settings.clickhouse_host}:{settings.clickhouse_http_port}"
    common = (base_url, settings.clickhouse_db, settings.clickhouse_user, settings.clickhouse_password)

    features_df = clickhouse_select(
        *common,
        "SELECT entity_key, domain, window_start, window_end, zscore, realized_vol, spread_bps, "
        "order_imbalance, mean_amount, decline_rate, distinct_accounts, throughput_eps, "
        "latency_p99_ms, error_rate FROM features",
    )
    events_df = clickhouse_select(*common, "SELECT entity_key, domain, ts_event, scenario_label FROM raw_events")

    if features_df.empty:
        print("no rows in risk.features yet - run the pipeline for a while first")
        return

    labels = label_features(features_df, events_df)
    print(f"loaded {len(features_df)} feature windows, {int(labels.sum())} labeled anomalous from {len(events_df)} raw events")

    for domain in ("market", "payments"):
        train_domain(domain, features_df, labels, args.min_rows)


if __name__ == "__main__":
    main()
