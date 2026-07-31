.PHONY: infra-up infra-down up down build logs ps clean \
        setup test test-rust test-python test-js \
        lint fmt \
        eval load-test demo

COMPOSE := docker compose
PY_SERVICES := ml-inference api-gateway data-generator

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

## --- Local dev environments (outside Docker) --------------------------------
## Each Python service manages its own venv (different dependency sets —
## torch/xgboost for ml-inference, nothing heavy for api-gateway); test/lint
## targets below call each venv's own binaries, not the system python3/ruff.

setup:
	@for svc in $(PY_SERVICES); do \
		echo "==> services/$$svc"; \
		(cd services/$$svc && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt); \
	done
	cd services/dashboard && npm install
	cd tests/eval && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
	cd tests/integration && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

## --- Tests ---------------------------------------------------------------

test: test-rust test-python test-js

test-rust:
	cd services/ingestion && cargo test
	cd services/feature-service && cargo test

test-python:
	@for svc in $(PY_SERVICES); do \
		echo "==> services/$$svc"; \
		(cd services/$$svc && .venv/bin/python -m pytest -q) || exit 1; \
	done

test-js:
	cd services/dashboard && npm test -- --run

## --- Quality ---------------------------------------------------------------

lint:
	cd services/ingestion && cargo clippy --all-targets -- -D warnings
	cd services/feature-service && cargo clippy --all-targets -- -D warnings
	@for svc in $(PY_SERVICES); do \
		echo "==> services/$$svc"; \
		(cd services/$$svc && .venv/bin/ruff check app $$([ -d scripts ] && echo scripts)) || exit 1; \
	done
	cd services/dashboard && npm run lint

fmt:
	cd services/ingestion && cargo fmt
	cd services/feature-service && cargo fmt
	@for svc in $(PY_SERVICES); do \
		(cd services/$$svc && .venv/bin/ruff format app $$([ -d scripts ] && echo scripts)); \
	done

## --- Evaluation / load test -------------------------------------------------
## Both hit the running stack over HTTP, so either venv works; tests/eval's
## own venv keeps them independent of any one service's dependency set.

eval:
	cd tests/eval && .venv/bin/python run_eval.py

load-test:
	cd tests/eval && .venv/bin/python ../../scripts/load_test.py

demo:
	bash scripts/demo.sh
