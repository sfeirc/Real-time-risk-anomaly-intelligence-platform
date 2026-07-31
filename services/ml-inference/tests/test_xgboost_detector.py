import numpy as np
import xgboost as xgb

from app.detectors.xgboost_detector import XGBoostDetector
from app.features import feature_names


def _train_and_save(domain: str, path, n_features: int = 7, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(200, n_features))
    y = (x[:, 0] > 0).astype(int)
    # mirrors scripts/train_xgboost.py: named features, or predict() later
    # raises ValueError on a mismatch (see app/detectors/xgboost_detector.py).
    dtrain = xgb.DMatrix(x, label=y, feature_names=feature_names(domain))
    booster = xgb.train({"objective": "binary:logistic", "max_depth": 2}, dtrain, num_boost_round=10)
    booster.save_model(str(path))


def test_missing_model_file_stays_not_ready(tmp_path):
    det = XGBoostDetector(str(tmp_path / "does_not_exist.json"), "market")
    assert not det.ready
    assert det.score([0.0] * 7) is None


def test_loaded_model_scores_and_becomes_ready(tmp_path):
    model_path = tmp_path / "xgb_test.json"
    _train_and_save("market", model_path)

    det = XGBoostDetector(str(model_path), "market")
    assert det.ready
    score = det.score([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert score is not None
    assert 0.0 <= score <= 1.0


def test_score_uses_domain_specific_feature_names(tmp_path):
    """A booster trained on payments' feature names must still score fine
    when the detector is constructed for the payments domain — this is
    exactly the mismatch that used to raise inside the executor thread and
    silently kill the Kafka consume loop (see app/main.py's blanket
    per-message exception handling, added to make that class of bug loud
    instead of silent)."""
    model_path = tmp_path / "xgb_payments.json"
    _train_and_save("payments", model_path)

    det = XGBoostDetector(str(model_path), "payments")
    score = det.score([0.5] * 7)
    assert score is not None


def test_reload_picks_up_model_written_after_construction(tmp_path):
    model_path = tmp_path / "xgb_test2.json"
    det = XGBoostDetector(str(model_path), "market")
    assert not det.ready

    _train_and_save("market", model_path, seed=1)

    assert det.reload() is True
    assert det.ready
    assert det.score([0.0] * 7) is not None
