import math
import random

from app.entities import MARKET_SYMBOLS, MERCHANTS
from app.generators import MarketEntity, PaymentsEntity
from app.scenarios import Scenario


def test_market_entity_produces_valid_tick():
    spec = MARKET_SYMBOLS[0]
    entity = MarketEntity(spec, random.Random(1))
    payload, corrupted = entity.tick(dt_s=0.5, scenario=None)
    assert not corrupted
    assert payload.symbol == spec.symbol
    assert payload.price > 0
    assert payload.bid < payload.ask
    assert payload.side in ("buy", "sell")
    assert payload.exchange_latency_ms > 0


def test_market_volatility_spike_widens_price_moves():
    spec = MARKET_SYMBOLS[0]
    rng_seed = 42

    baseline = MarketEntity(spec, random.Random(rng_seed))
    spiked = MarketEntity(spec, random.Random(rng_seed))
    scenario = Scenario(
        scenario_type="volatility_spike",
        domain="market",
        entity_key=spec.symbol,
        start_ts=0.0,
        duration_s=60.0,
        params={"vol_multiplier": 10.0},
    )

    baseline_moves, spiked_moves = [], []
    for _ in range(500):
        p0 = baseline.price
        payload, _ = baseline.tick(dt_s=1.0, scenario=None)
        baseline_moves.append(abs(math.log(payload.price / p0)))

        p0 = spiked.price
        payload, _ = spiked.tick(dt_s=1.0, scenario=scenario)
        spiked_moves.append(abs(math.log(payload.price / p0)))

    assert sum(spiked_moves) / len(spiked_moves) > sum(baseline_moves) / len(baseline_moves) * 2


def test_market_data_corruption_produces_out_of_range_values():
    spec = MARKET_SYMBOLS[0]
    entity = MarketEntity(spec, random.Random(7))
    scenario = Scenario(
        scenario_type="data_corruption",
        domain="market",
        entity_key=spec.symbol,
        start_ts=0.0,
        duration_s=60.0,
        params={"corruption_rate": 1.0},  # always corrupt for a deterministic test
    )
    saw_corruption = False
    for _ in range(20):
        payload, corrupted = entity.tick(dt_s=0.5, scenario=scenario)
        if corrupted:
            saw_corruption = True
            assert payload.price < 0 or payload.bid > payload.ask or payload.exchange_latency_ms > 1000 or payload.size == 0.0
    assert saw_corruption


def test_regime_shift_persists_after_call():
    spec = MARKET_SYMBOLS[0]
    entity = MarketEntity(spec, random.Random(3))
    original_vol = entity.live_annual_vol
    entity.apply_regime_shift({"vol_multiplier": 2.0, "drift_shift": 0.1})
    assert entity.live_annual_vol == original_vol * 2.0
    # persists across further ticks with no active scenario
    entity.tick(dt_s=1.0, scenario=None)
    assert entity.live_annual_vol == original_vol * 2.0


def test_payments_entity_produces_valid_txn():
    spec = MERCHANTS[0]
    entity = PaymentsEntity(spec, random.Random(1))
    payload, corrupted = entity.tick(scenario=None)
    assert not corrupted
    assert payload.merchant_id == spec.merchant_id
    assert payload.amount > 0
    assert payload.status in ("approved", "declined", "error")
    assert len(payload.account_id_hash) == 16


def test_fraud_pattern_concentrates_on_compromised_accounts():
    spec = MERCHANTS[1]
    entity = PaymentsEntity(spec, random.Random(5))
    scenario = Scenario(
        scenario_type="fraud_pattern",
        domain="payments",
        entity_key=spec.merchant_id,
        start_ts=0.0,
        duration_s=30.0,
        params={
            "amount_multiplier": 5.0,
            "cross_border_prob": 0.9,
            "decline_rate_override": 0.3,
            "compromised_fraction": 0.02,
        },
    )
    accounts_seen = set()
    amounts = []
    for _ in range(200):
        payload, _ = entity.tick(scenario=scenario)
        accounts_seen.add(payload.account_id_hash)
        amounts.append(payload.amount)

    expected_pool = max(1, int(len(entity.customer_pool) * 0.02))
    assert len(accounts_seen) <= expected_pool
    assert sum(amounts) / len(amounts) > spec.mean_amount * 2


def test_fraud_pattern_clears_after_scenario_ends():
    spec = MERCHANTS[1]
    entity = PaymentsEntity(spec, random.Random(5))
    scenario = Scenario(
        scenario_type="fraud_pattern", domain="payments", entity_key=spec.merchant_id,
        start_ts=0.0, duration_s=30.0,
        params={"amount_multiplier": 5.0, "cross_border_prob": 0.9, "decline_rate_override": 0.3, "compromised_fraction": 0.02},
    )
    entity.tick(scenario=scenario)
    assert entity.compromised
    entity.tick(scenario=None)
    assert not entity.compromised
