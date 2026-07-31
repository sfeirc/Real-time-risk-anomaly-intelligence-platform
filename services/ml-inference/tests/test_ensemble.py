from app.ensemble import combine
from app.schemas import DetectorScores


def test_all_zero_scores_zero():
    d = DetectorScores(zscore=0, ewma=0, cusum=0, isolation_forest=0, autoencoder=0, xgboost=None)
    assert combine(d) == 0.0


def test_all_one_scores_one():
    d = DetectorScores(zscore=1, ewma=1, cusum=1, isolation_forest=1, autoencoder=1, xgboost=None)
    assert abs(combine(d) - 1.0) < 1e-9


def test_result_bounded_in_unit_interval():
    d = DetectorScores(zscore=0.9, ewma=0.1, cusum=0.5, isolation_forest=0.3, autoencoder=0.8, xgboost=0.95)
    score = combine(d)
    assert 0.0 <= score <= 1.0


def test_xgboost_present_shifts_score_toward_its_value():
    without = DetectorScores(zscore=0.2, ewma=0.2, cusum=0.2, isolation_forest=0.2, autoencoder=0.2, xgboost=None)
    with_high_xgb = DetectorScores(zscore=0.2, ewma=0.2, cusum=0.2, isolation_forest=0.2, autoencoder=0.2, xgboost=0.95)
    assert combine(with_high_xgb) > combine(without)


def test_single_confident_detector_pulls_score_up_but_does_not_dominate_alone():
    d = DetectorScores(zscore=1.0, ewma=0.0, cusum=0.0, isolation_forest=0.0, autoencoder=0.0, xgboost=None)
    score = combine(d)
    assert 0.0 < score < 0.5, "one maxed-out detector among five should raise but not saturate the ensemble"
