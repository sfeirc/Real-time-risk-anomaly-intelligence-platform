from datetime import datetime, timedelta

from eval_lib import Window, confusion_by_scenario, confusion_matrix, extract_episodes, mean_detection_delay_s

T0 = datetime(2026, 1, 1, 0, 0, 0)


def w(entity, i, label, alerted, domain="market", step_s=2):
    return Window(
        entity_key=entity,
        domain=domain,
        window_start=T0 + timedelta(seconds=i * step_s),
        window_end=T0 + timedelta(seconds=(i + 1) * step_s),
        scenario_label=label,
        alerted=alerted,
    )


def test_confusion_matrix_all_four_quadrants():
    windows = [
        w("e1", 0, "volatility_spike", True),  # TP
        w("e1", 1, "volatility_spike", False),  # FN
        w("e1", 2, None, True),  # FP
        w("e1", 3, None, False),  # TN
    ]
    c = confusion_matrix(windows)
    assert (c.tp, c.fp, c.fn, c.tn) == (1, 1, 1, 1)
    assert c.precision == 0.5
    assert c.recall == 0.5
    assert c.false_positive_rate == 0.5


def test_confusion_matrix_empty_denominators_are_none_not_zero():
    c = confusion_matrix([w("e1", 0, None, False)])
    assert c.precision is None  # no positive predictions at all
    assert c.recall is None  # no ground-truth positives at all


def test_confusion_by_scenario_splits_recall_per_label():
    windows = [
        w("e1", 0, "fraud_pattern", True),
        w("e1", 1, "fraud_pattern", False),
        w("e2", 0, "latency_incident", True),
    ]
    by_scenario = confusion_by_scenario(windows)
    assert by_scenario["fraud_pattern"].recall == 0.5
    assert by_scenario["latency_incident"].recall == 1.0


def test_extract_episodes_groups_contiguous_same_label_windows():
    windows = [
        w("e1", 0, "regime_change", False),
        w("e1", 1, "regime_change", True),
        w("e1", 2, "regime_change", False),
        w("e1", 3, None, False),
    ]
    episodes = extract_episodes(windows)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.scenario_label == "regime_change"
    assert ep.start == windows[0].window_start
    assert ep.end == windows[2].window_end
    assert ep.detected_at == windows[1].window_end


def test_extract_episodes_separates_non_contiguous_runs_of_same_label():
    windows = [
        w("e1", 0, "volume_spike", False),
        w("e1", 1, None, False),
        w("e1", 2, "volume_spike", True),
    ]
    episodes = extract_episodes(windows)
    assert len(episodes) == 2
    assert episodes[0].detected_at is None
    assert episodes[1].detected_at is not None


def test_extract_episodes_separates_different_entities():
    windows = [
        w("e1", 0, "fraud_pattern", True),
        w("e2", 0, "fraud_pattern", True),
    ]
    episodes = extract_episodes(windows)
    assert len(episodes) == 2
    assert {ep.entity_key for ep in episodes} == {"e1", "e2"}


def test_episode_never_detected_has_none_delay():
    windows = [w("e1", 0, "data_corruption", False), w("e1", 1, "data_corruption", False)]
    episodes = extract_episodes(windows)
    assert episodes[0].detected_at is None


def test_mean_detection_delay_averages_within_scenario_and_ignores_missed():
    windows_a = [w("e1", 0, "fraud_pattern", False), w("e1", 1, "fraud_pattern", True)]  # delay = 4s (2 windows * 2s step, detected at window 1's end)
    windows_b = [w("e2", 0, "fraud_pattern", True)]  # delay = 2s
    episodes = extract_episodes(windows_a) + extract_episodes(windows_b)
    delays = mean_detection_delay_s(episodes)
    assert delays["fraud_pattern"] == 3.0  # (4 + 2) / 2


def test_mean_detection_delay_omits_scenario_with_zero_detections():
    windows = [w("e1", 0, "data_corruption", False)]
    episodes = extract_episodes(windows)
    delays = mean_detection_delay_s(episodes)
    assert "data_corruption" not in delays
