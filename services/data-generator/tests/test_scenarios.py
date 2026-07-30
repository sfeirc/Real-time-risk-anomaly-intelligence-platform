from app.scenarios import MARKET_SCENARIOS, PAYMENTS_SCENARIOS, ScenarioManager


def test_no_spawn_when_probability_zero():
    mgr = ScenarioManager(probability_per_tick=0.0, seed=1)
    for t in range(100):
        mgr.maybe_spawn("market", "BTC-USD", now=float(t))
    assert mgr.get_active("market", "BTC-USD", now=100.0) is None


def test_always_spawns_when_probability_one():
    mgr = ScenarioManager(probability_per_tick=1.0, seed=1)
    sc = mgr.maybe_spawn("market", "BTC-USD", now=0.0)
    assert sc is not None
    assert sc.scenario_type in {"volatility_spike", "latency_incident", "data_corruption", "regime_change", "volume_spike"}


def test_does_not_double_spawn_while_active():
    mgr = ScenarioManager(probability_per_tick=1.0, seed=1)
    first = mgr.maybe_spawn("market", "BTC-USD", now=0.0)
    second = mgr.maybe_spawn("market", "BTC-USD", now=0.1)
    assert first is not None
    assert second is None


def test_scenario_expires_after_duration():
    mgr = ScenarioManager(probability_per_tick=1.0, seed=1)
    sc = mgr.maybe_spawn("market", "BTC-USD", now=0.0)
    assert mgr.get_active("market", "BTC-USD", now=sc.duration_s - 0.01) is not None
    assert mgr.get_active("market", "BTC-USD", now=sc.duration_s + 0.01) is None


def test_inject_forces_specific_scenario_and_duration():
    mgr = ScenarioManager(probability_per_tick=0.0, seed=1)
    sc = mgr.inject("payments", "merch_grocery_01", "fraud_pattern", duration_s=15.0, now=0.0)
    assert sc.scenario_type == "fraud_pattern"
    assert sc.duration_s == 15.0
    active = mgr.get_active("payments", "merch_grocery_01", now=10.0)
    assert active is sc


def test_regime_change_callback_invoked_once_on_spawn():
    calls = []
    mgr = ScenarioManager(probability_per_tick=0.0, seed=1)
    mgr.on_regime_change = lambda domain, key, params: calls.append((domain, key, params))
    mgr.inject("market", "ETH-USD", "regime_change", now=0.0)
    assert len(calls) == 1
    assert calls[0][0] == "market"
    assert calls[0][1] == "ETH-USD"


def test_domain_scenario_pools_are_disjoint_on_domain_specific_types():
    assert "volatility_spike" in MARKET_SCENARIOS and "volatility_spike" not in PAYMENTS_SCENARIOS
    assert "fraud_pattern" in PAYMENTS_SCENARIOS and "fraud_pattern" not in MARKET_SCENARIOS


def test_maybe_spawn_for_payments_never_yields_volatility_spike():
    mgr = ScenarioManager(probability_per_tick=1.0, seed=2)
    for i in range(30):
        sc = mgr.maybe_spawn("payments", f"m{i}", now=0.0)
        assert sc is not None
        assert sc.scenario_type != "volatility_spike"
