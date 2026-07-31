from app.rules import RulesEngine


def test_below_watch_threshold_no_alert():
    engine = RulesEngine("app/rules.yaml")
    severity, action = engine.evaluate("market", 0.1)
    assert severity is None
    assert action is None


def test_market_critical_does_not_block():
    engine = RulesEngine("app/rules.yaml")
    severity, action = engine.evaluate("market", 0.95)
    assert severity == "critical"
    assert action == "alert", "market has no order to reject in this demo; critical should not imply block"


def test_payments_critical_blocks():
    engine = RulesEngine("app/rules.yaml")
    severity, action = engine.evaluate("payments", 0.90)
    assert severity == "critical"
    assert action == "block"


def test_payments_has_a_lower_block_bar_than_market():
    engine = RulesEngine("app/rules.yaml")
    # a score that is watch/alert-only for market should already be
    # critical for payments, reflecting the fraud cost asymmetry.
    severity_payments, _ = engine.evaluate("payments", 0.87)
    severity_market, _ = engine.evaluate("market", 0.87)
    assert severity_payments == "critical"
    assert severity_market == "alert"


def test_unknown_domain_falls_back_to_default():
    engine = RulesEngine("app/rules.yaml")
    severity, action = engine.evaluate("unknown_domain", 0.95)
    assert severity == "critical"
    assert action == "block"
