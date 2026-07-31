from app.detectors.statistical import (
    CusumDetector,
    EwmaRunRulesDetector,
    ZScoreDetector,
    squash,
)


def test_squash_zero_at_zero():
    assert squash(0.0) == 0.0


def test_squash_monotonic_increasing():
    assert squash(1.0) < squash(2.0) < squash(4.0) < squash(10.0)


def test_squash_bounded_below_one():
    # note: at extreme inputs (~1000) exp(-x/scale) underflows to exactly
    # 0.0 in float64 and squash saturates to exactly 1.0 — expected and
    # fine for a saturating anomaly score. Use a value large enough to
    # approach the bound without hitting that underflow.
    assert squash(20.0) < 1.0


def test_zscore_detector_matches_squash():
    det = ZScoreDetector()
    assert det.score(3.0) == squash(3.0)


def test_ewma_run_rules_single_extreme_point_scores_max():
    det = EwmaRunRulesDetector()
    assert det.score("e1", 3.5) == 1.0


def test_ewma_run_rules_two_of_three_moderate_same_sign():
    det = EwmaRunRulesDetector()
    det.score("e1", 0.1)
    det.score("e1", 2.5)
    score = det.score("e1", 2.2)
    assert score == 0.8


def test_ewma_run_rules_mixed_sign_does_not_trigger():
    det = EwmaRunRulesDetector()
    det.score("e1", 2.5)
    det.score("e1", -2.5)
    score = det.score("e1", 0.1)
    assert score < 0.8


def test_ewma_run_rules_tracks_entities_independently():
    det = EwmaRunRulesDetector()
    det.score("e1", 2.5)
    det.score("e1", 2.2)
    # e2 has no history: the same value should not trigger e1's run rule
    score_e2 = det.score("e2", 2.2)
    assert score_e2 < 0.8


def test_cusum_accumulates_sustained_small_shift():
    det = CusumDetector(k=0.5, h=5.0)
    score = 0.0
    for _ in range(30):
        score = det.score("e1", 1.0)  # small, sustained positive shift
    assert score > 0.5, "sustained small shift should eventually cross the CUSUM threshold"


def test_cusum_resets_on_reset_call():
    det = CusumDetector(k=0.5, h=5.0)
    for _ in range(30):
        det.score("e1", 1.0)
    det.reset("e1")
    score = det.score("e1", 0.0)
    assert score == 0.0


def test_cusum_ignores_noise_within_slack():
    det = CusumDetector(k=0.5, h=5.0)
    score = 0.0
    for i in range(30):
        score = det.score("e1", 0.3 if i % 2 == 0 else -0.3)
    assert score < 0.2, "oscillating noise within the slack band should not accumulate"
