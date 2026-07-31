import numpy as np

from app.detectors.autoencoder import RollingAutoencoder


def _make(min_buffer=60):
    return RollingAutoencoder(
        buffer_size=500, min_buffer=min_buffer, retrain_every=60,
        hidden_dim=6, latent_dim=2, epochs=30, lr=0.02,
    )


def test_not_ready_before_min_buffer():
    det = _make(min_buffer=60)
    for _ in range(59):
        det.observe([0.0] * 5)
    assert not det.ready
    assert det.score([0.0] * 5) == 0.0


def test_ready_after_min_buffer():
    det = _make(min_buffer=60)
    rng = np.random.default_rng(0)
    for _ in range(60):
        det.observe(rng.normal(size=5).tolist())
    assert det.ready


def test_out_of_distribution_point_has_higher_reconstruction_error():
    det = _make(min_buffer=80)
    rng = np.random.default_rng(0)
    # train on a low-rank manifold (first two dims correlated, rest ~0) so an
    # anomaly that breaks the correlation is genuinely hard to reconstruct
    for _ in range(150):
        a = rng.normal()
        det.observe([a, a * 0.9 + rng.normal(scale=0.05), 0.0, 0.0, 0.0])

    normal_score = det.score([0.5, 0.45, 0.0, 0.0, 0.0])
    anomaly_score = det.score([5.0, -5.0, 3.0, -3.0, 4.0])
    assert anomaly_score > normal_score
