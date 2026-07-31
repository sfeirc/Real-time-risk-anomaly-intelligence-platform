def test_list_alerts_default_query(client, fake_clickhouse):
    fake_clickhouse.next_rows = [{"alert_id": "1"}]
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    assert resp.json() == [{"alert_id": "1"}]
    query, params = fake_clickhouse.calls[0]
    assert "FROM alerts" in query
    assert params["since_minutes"] == 60
    assert params["limit"] == 100
    assert "domain" not in params


def test_list_alerts_applies_domain_and_severity_filters(client, fake_clickhouse):
    fake_clickhouse.next_rows = []
    resp = client.get("/api/alerts", params={"domain": "payments", "severity": "critical", "entity_key": "merch_1"})
    assert resp.status_code == 200
    query, params = fake_clickhouse.calls[0]
    assert "domain = {domain:String}" in query
    assert "severity = {severity:String}" in query
    assert "entity_key = {entity_key:String}" in query
    assert params["domain"] == "payments"
    assert params["severity"] == "critical"
    assert params["entity_key"] == "merch_1"


def test_list_alerts_rejects_invalid_domain(client):
    resp = client.get("/api/alerts", params={"domain": "not_a_domain"})
    assert resp.status_code == 422


def test_list_alerts_rejects_limit_over_max(client):
    resp = client.get("/api/alerts", params={"limit": 5000})
    assert resp.status_code == 422


def test_alerts_rollup_queries_rollup_table(client, fake_clickhouse):
    fake_clickhouse.next_rows = []
    resp = client.get("/api/alerts/rollup")
    assert resp.status_code == 200
    query, _ = fake_clickhouse.calls[0]
    assert "alerts_rollup_5m" in query


def test_probable_causes_queries_rollup_table(client, fake_clickhouse):
    fake_clickhouse.next_rows = []
    resp = client.get("/api/alerts/causes")
    assert resp.status_code == 200
    query, _ = fake_clickhouse.calls[0]
    assert "probable_cause_rollup_1h" in query


def test_latest_model_metrics(client, fake_clickhouse):
    fake_clickhouse.next_rows = [{"model_id": "ensemble-market"}]
    resp = client.get("/api/model-metrics/latest")
    assert resp.status_code == 200
    assert resp.json() == [{"model_id": "ensemble-market"}]


def test_model_metrics_history_requires_model_id(client):
    resp = client.get("/api/model-metrics/history")
    assert resp.status_code == 422


def test_throughput(client, fake_clickhouse):
    fake_clickhouse.next_rows = [{"bucket": "2026-01-01T00:00:00", "events": 10}]
    resp = client.get("/api/throughput")
    assert resp.status_code == 200
    query, params = fake_clickhouse.calls[0]
    assert "throughput_rollup_1m" in query
    assert params["since_minutes"] == 30


def test_entities_proxies_data_generator(client, fake_http_client):
    fake_http_client.next_json = {"market": ["BTC-USD"], "payments": []}
    resp = client.get("/api/entities")
    assert resp.status_code == 200
    assert resp.json()["market"] == ["BTC-USD"]
    method, url, _ = fake_http_client.calls[0]
    assert method == "GET"
    assert url.endswith("/entities")


def test_inject_scenario_proxies_post_with_body(client, fake_http_client, operator_token):
    fake_http_client.next_json = {"status": "injected"}
    resp = client.post(
        "/api/scenarios/inject",
        json={"domain": "market", "entity_key": "BTC-USD", "scenario": "volatility_spike"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "injected"
    method, _url, kwargs = fake_http_client.calls[0]
    assert method == "POST"
    assert kwargs["json"]["entity_key"] == "BTC-USD"


def test_inject_scenario_requires_operator_token(client):
    resp = client.post(
        "/api/scenarios/inject",
        json={"domain": "market", "entity_key": "BTC-USD", "scenario": "volatility_spike"},
    )
    assert resp.status_code == 401


def test_inject_scenario_rejects_garbage_token(client):
    resp = client.post(
        "/api/scenarios/inject",
        json={"domain": "market", "entity_key": "BTC-USD", "scenario": "volatility_spike"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
