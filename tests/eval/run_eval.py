#!/usr/bin/env python3
"""Precision/recall/F1/false-positive-rate + mean detection delay, per
scenario type and overall, computed from the live system's own ClickHouse
tables — not hand-typed. See eval_lib.py for the confusion-matrix/episode
logic (unit tested there) and docs/metrics.md for the metric definitions.

Usage:
    python run_eval.py [--since-hours 6] [--out ../../docs/benchmarks/latest.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from eval_lib import Window, confusion_by_scenario, confusion_matrix, extract_episodes, mean_detection_delay_s  # noqa: E402

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_HTTP_PORT = os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "risk")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")


def clickhouse_select(query: str) -> pd.DataFrame:
    base_url = f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_HTTP_PORT}"
    resp = httpx.post(
        f"{base_url}/",
        params={
            "database": CLICKHOUSE_DB,
            "query": f"{query} FORMAT JSONEachRow",
            "output_format_json_quote_64bit_integers": "0",
        },
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=120,
    )
    resp.raise_for_status()
    rows = [json.loads(line) for line in resp.text.strip().splitlines() if line]
    return pd.DataFrame(rows)


def label_windows(features_df: pd.DataFrame, events_df: pd.DataFrame, alerted_keys: set[tuple[str, str]]) -> list[Window]:
    labeled_events = events_df[events_df["scenario_label"] != ""].copy()
    labeled_events["ts_event_dt"] = pd.to_datetime(labeled_events["ts_event"])
    window_start = pd.to_datetime(features_df["window_start"])
    window_end = pd.to_datetime(features_df["window_end"])

    windows: list[Window] = []
    for entity, group in features_df.groupby("entity_key"):
        ev = labeled_events[labeled_events["entity_key"] == entity]
        ev_times = ev["ts_event_dt"].to_numpy()
        ev_labels = ev["scenario_label"].to_numpy()
        for idx, row in group.iterrows():
            s, e = window_start[idx].to_datetime64(), window_end[idx].to_datetime64()
            mask = (ev_times >= s) & (ev_times < e)
            label = str(ev_labels[mask][0]) if mask.any() else None
            alerted = (row["entity_key"], row["window_end"]) in alerted_keys
            windows.append(
                Window(
                    entity_key=row["entity_key"],
                    domain=row["domain"],
                    window_start=window_start[idx].to_pydatetime(),
                    window_end=window_end[idx].to_pydatetime(),
                    scenario_label=label,
                    alerted=alerted,
                )
            )
    windows.sort(key=lambda w: (w.entity_key, w.window_start))
    return windows


def build_report(windows: list[Window], since_hours: float) -> dict:
    overall = confusion_matrix(windows)
    by_scenario_conf = confusion_by_scenario(windows)
    episodes = extract_episodes(windows)
    delays = mean_detection_delay_s(episodes)

    episodes_by_scenario: dict[str, dict] = {}
    for label, conf in by_scenario_conf.items():
        eps = [e for e in episodes if e.scenario_label == label]
        detected = sum(1 for e in eps if e.detected_at is not None)
        episodes_by_scenario[label] = {
            "episodes": len(eps),
            "episodes_detected": detected,
            "episode_detection_rate": detected / len(eps) if eps else None,
            "window_recall": conf.recall,
            "mean_detection_delay_s": delays.get(label),
        }

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "eval_window_hours": since_hours,
        "windows_evaluated": len(windows),
        "overall": {
            "precision": overall.precision,
            "recall": overall.recall,
            "f1": overall.f1,
            "false_positive_rate": overall.false_positive_rate,
            "tp": overall.tp,
            "fp": overall.fp,
            "fn": overall.fn,
            "tn": overall.tn,
        },
        "by_scenario": episodes_by_scenario,
    }


def print_summary(report: dict) -> None:
    o = report["overall"]

    def fmt(x):
        return f"{x:.3f}" if isinstance(x, float) else "n/a"

    print(f"\nEvaluated {report['windows_evaluated']} feature windows over the last {report['eval_window_hours']}h\n")
    print(f"{'':20s} {'precision':>10s} {'recall':>10s} {'f1':>10s} {'fpr':>10s}")
    print(f"{'OVERALL':20s} {fmt(o['precision']):>10s} {fmt(o['recall']):>10s} {fmt(o['f1']):>10s} {fmt(o['false_positive_rate']):>10s}")
    print(f"  (tp={o['tp']} fp={o['fp']} fn={o['fn']} tn={o['tn']})\n")

    print(f"{'scenario':20s} {'episodes':>10s} {'detected':>10s} {'det. rate':>10s} {'mean delay':>12s}")
    for label, s in report["by_scenario"].items():
        delay = f"{s['mean_detection_delay_s']:.1f}s" if s["mean_detection_delay_s"] is not None else "n/a"
        rate = fmt(s["episode_detection_rate"])
        print(f"{label:20s} {s['episodes']:>10d} {s['episodes_detected']:>10d} {rate:>10s} {delay:>12s}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-hours", type=float, default=6.0)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "docs", "benchmarks", "latest.json"))
    args = parser.parse_args()

    features_df = clickhouse_select(
        f"SELECT entity_key, domain, window_start, window_end FROM features "
        f"WHERE window_end > now() - INTERVAL {int(args.since_hours * 3600)} SECOND"
    )
    if features_df.empty:
        print("no feature windows in the requested range — run the pipeline for a while first")
        return

    events_df = clickhouse_select(
        f"SELECT entity_key, domain, ts_event, scenario_label FROM raw_events "
        f"WHERE ts_ingest > now() - INTERVAL {int(args.since_hours * 3600)} SECOND"
    )
    alerts_df = clickhouse_select(
        f"SELECT entity_key, window_end FROM alerts WHERE ts > now() - INTERVAL {int(args.since_hours * 3600)} SECOND"
    )
    alerted_keys = set(zip(alerts_df.get("entity_key", []), alerts_df.get("window_end", [])))

    windows = label_windows(features_df, events_df, alerted_keys)
    report = build_report(windows, args.since_hours)

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print_summary(report)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
