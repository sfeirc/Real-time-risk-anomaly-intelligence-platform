from app.detectors.velocity import RollingVelocity


def test_rolling_sum_accumulates_within_the_window():
    v = RollingVelocity(window_count=3)
    assert v.observe_and_get("BTC-USD", 10) == 10
    assert v.observe_and_get("BTC-USD", 20) == 30
    assert v.observe_and_get("BTC-USD", 30) == 60


def test_rolling_sum_drops_values_older_than_the_window():
    v = RollingVelocity(window_count=3)
    v.observe_and_get("BTC-USD", 10)
    v.observe_and_get("BTC-USD", 20)
    v.observe_and_get("BTC-USD", 30)
    # a 4th observation should evict the first (10), not just keep growing
    assert v.observe_and_get("BTC-USD", 40) == 90  # 20 + 30 + 40


def test_entities_are_tracked_independently():
    v = RollingVelocity(window_count=5)
    assert v.observe_and_get("BTC-USD", 100) == 100
    assert v.observe_and_get("ETH-USD", 1) == 1
    assert v.observe_and_get("BTC-USD", 100) == 200
