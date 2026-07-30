.PHONY: infra-up infra-down up down build logs ps clean \
        test test-rust test-python test-js \
        lint fmt \
        eval load-test demo

COMPOSE := docker compose

## Bring up only the infra layer (Redpanda, ClickHouse, Prometheus, Grafana)
infra-up:
	$(COMPOSE) up -d redpanda redpanda-console redpanda-topics-init clickhouse prometheus grafana

infra-down:
	$(COMPOSE) stop redpanda redpanda-console clickhouse prometheus grafana

## Full stack: infra + every application service + dashboard
up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v --remove-orphans

## --- Tests ---------------------------------------------------------------

test: test-rust test-python test-js

test-rust:
	cd services/ingestion && cargo test
	cd services/feature-service && cargo test

test-python:
	cd services/ml-inference && python3 -m pytest -q
	cd services/api-gateway && python3 -m pytest -q
	cd services/data-generator && python3 -m pytest -q

test-js:
	cd services/dashboard && npm test -- --run

## --- Quality ---------------------------------------------------------------

lint:
	cd services/ingestion && cargo clippy -- -D warnings
	cd services/feature-service && cargo clippy -- -D warnings
	cd services/ml-inference && ruff check app
	cd services/api-gateway && ruff check app
	cd services/data-generator && ruff check app
	cd services/dashboard && npm run lint

fmt:
	cd services/ingestion && cargo fmt
	cd services/feature-service && cargo fmt
	cd services/ml-inference && ruff format app
	cd services/api-gateway && ruff format app
	cd services/data-generator && ruff format app
	cd services/dashboard && npm run format

## --- Evaluation / load test -------------------------------------------------

eval:
	python3 tests/eval/run_eval.py

load-test:
	python3 scripts/load_test.py

demo:
	bash scripts/demo.sh
