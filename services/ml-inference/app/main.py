from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from . import metrics, telemetry
from .clickhouse_io import ClickHouseSink
from .config import settings
from .kafka_io import make_consumer, make_producer
from .pipeline import DOMAINS, MLPipeline
from .schemas import AlertEvent, FeatureEvent

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("ml-inference")

telemetry.init("ml-inference", settings.otel_exporter_otlp_endpoint)
tracer = trace.get_tracer("ml-inference")

pipeline = MLPipeline(settings)

# `MLPipeline.process`/`check_drift_and_build_metrics` are synchronous and
# occasionally expensive (Isolation Forest / autoencoder retrain every
# `retrain_every_n_windows` windows briefly costs ~100s of ms of pure numpy/
# sklearn/torch CPU work). Run them off the event loop so a retrain doesn't
# stall Kafka consumption or the /metrics and /health endpoints for its
# duration. A single worker (not a pool) is deliberate: it serializes every
# call that touches shared detector state, which is what makes it safe to
# call from both the consume loop and the periodic model-metrics loop
# without a lock.
_pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ml-pipeline")

_dead_tasks: set[str] = set()


def _on_background_task_done(name: str, task: asyncio.Task) -> None:
    if task.cancelled():
        return  # normal shutdown path
    exc = task.exception()
    metrics.background_task_alive.labels(task=name).set(0)
    _dead_tasks.add(name)
    if exc is not None:
        log.critical("background task %s died with an exception", name, exc_info=exc)
    else:
        log.critical("background task %s exited unexpectedly (should run forever)", name)


async def _consume_loop(consumer, producer, ch_sink: ClickHouseSink, alert_queue: asyncio.Queue[AlertEvent]) -> None:
    loop = asyncio.get_running_loop()
    async for msg in consumer:
        try:
            f = FeatureEvent.model_validate(msg.value)
        except Exception as e:  # noqa: BLE001 - one malformed message must not kill the consumer loop
            metrics.parse_errors_total.inc()
            log.warning("failed to parse feature event: %s", e)
            continue

        metrics.features_consumed_total.labels(domain=f.domain).inc()

        # Continues the trace feature-service started for this window (see
        # services/feature-service/src/consumer.rs's compute_window span),
        # so the whole ingest -> window -> score -> alert path is one trace
        # in Jaeger, not four disconnected ones.
        parent_cx = telemetry.extract_parent_context(msg.headers)
        with tracer.start_as_current_span("score_window", context=parent_cx):
            started = loop.time()
            try:
                alert = await loop.run_in_executor(_pipeline_executor, pipeline.process, f)
            except Exception:
                # A detector bug on one message must not silently kill Kafka
                # consumption for every message after it (see git history: an
                # unnamed-vs-named-feature XGBoost mismatch did exactly this —
                # the exception propagated out of this un-caught executor call,
                # the `async for` loop died with no log line, and the consumer
                # only reappeared as a silent group departure ~5 minutes later
                # via Kafka's max.poll.interval.ms).
                metrics.processing_errors_total.labels(domain=f.domain).inc()
                log.exception("pipeline.process raised for entity=%s domain=%s", f.entity_key, f.domain)
                continue
            metrics.inference_ms.observe((loop.time() - started) * 1000.0)

            if alert is None:
                continue

            metrics.alerts_produced_total.labels(domain=alert.domain, severity=alert.severity).inc()
            try:
                await producer.send_and_wait(
                    settings.kafka_topic_alerts,
                    key=alert.entity_key,
                    value=alert.model_dump(mode="json"),
                    headers=telemetry.inject_current_context(),
                )
            except Exception as e:  # noqa: BLE001 - log and keep serving; ClickHouse still gets the row
                metrics.kafka_produce_errors_total.inc()
                log.warning("failed to produce alert: %s", e)

            await alert_queue.put(alert)


async def _clickhouse_flush_loop(ch_sink: ClickHouseSink, alert_queue: asyncio.Queue[AlertEvent]) -> None:
    while True:
        batch: list[AlertEvent] = []
        try:
            first = await asyncio.wait_for(alert_queue.get(), timeout=settings.clickhouse_flush_interval_s)
            batch.append(first)
        except asyncio.TimeoutError:
            pass

        while len(batch) < settings.clickhouse_batch_size:
            try:
                batch.append(alert_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            continue
        try:
            await ch_sink.insert_alerts(batch)
            metrics.clickhouse_rows_written_total.labels(table="alerts").inc(len(batch))
        except Exception as e:  # noqa: BLE001 - a dropped batch shouldn't take the service down
            metrics.clickhouse_write_errors_total.inc()
            log.warning("clickhouse alerts batch insert failed, dropping %d rows: %s", len(batch), e)


async def _model_metrics_loop(producer, ch_sink: ClickHouseSink) -> None:
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(settings.model_metrics_interval_s)
        for domain in DOMAINS:
            try:
                event = await loop.run_in_executor(_pipeline_executor, pipeline.check_drift_and_build_metrics, domain)
            except Exception:
                log.exception("check_drift_and_build_metrics raised for domain=%s", domain)
                continue
            metrics.drift_detected_gauge.labels(domain=domain).set(1 if event.drift_detected else 0)
            metrics.model_ready.labels(domain=domain, model="isolation_forest").set(1 if pipeline.iso_forests[domain].ready else 0)
            metrics.model_ready.labels(domain=domain, model="autoencoder").set(1 if pipeline.autoencoders[domain].ready else 0)
            metrics.model_ready.labels(domain=domain, model="xgboost").set(1 if pipeline.xgboost_detectors[domain].ready else 0)
            try:
                await producer.send_and_wait(settings.kafka_topic_model_metrics, key=domain, value=event.model_dump(mode="json"))
            except Exception as e:  # noqa: BLE001
                metrics.kafka_produce_errors_total.inc()
                log.warning("failed to produce model-metrics event: %s", e)
            try:
                await ch_sink.insert_model_metrics([event])
                metrics.clickhouse_rows_written_total.labels(table="model_metrics").inc()
            except Exception as e:  # noqa: BLE001
                metrics.clickhouse_write_errors_total.inc()
                log.warning("clickhouse model_metrics insert failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = await make_consumer(settings.kafka_brokers, settings.kafka_topic_features, settings.kafka_consumer_group)
    producer = await make_producer(settings.kafka_brokers)
    ch_sink = ClickHouseSink(
        f"http://{settings.clickhouse_host}:{settings.clickhouse_http_port}",
        settings.clickhouse_db,
        settings.clickhouse_user,
        settings.clickhouse_password,
    )
    alert_queue: asyncio.Queue[AlertEvent] = asyncio.Queue()

    named_coros = {
        "consume_loop": _consume_loop(consumer, producer, ch_sink, alert_queue),
        "clickhouse_flush_loop": _clickhouse_flush_loop(ch_sink, alert_queue),
        "model_metrics_loop": _model_metrics_loop(producer, ch_sink),
    }
    tasks = []
    for name, coro in named_coros.items():
        metrics.background_task_alive.labels(task=name).set(1)
        task = asyncio.create_task(coro, name=name)
        # these coroutines are `while True`/`async for ... :` loops that are
        # only ever supposed to exit on cancellation at shutdown; per-item
        # errors are already caught inside each loop (see _consume_loop /
        # _model_metrics_loop), so a task finishing here on its own means an
        # unanticipated bug got past that and the loop is dead — make that
        # loud (CRITICAL log + a metric /health can key off) instead of the
        # silent, hours-later-discovered failure this class of bug caused
        # once already (see the _consume_loop comment on max.poll.interval.ms).
        task.add_done_callback(lambda t, name=name: _on_background_task_done(name, t))
        tasks.append(task)
    log.info("ml-inference started: brokers=%s topic_in=%s", settings.kafka_brokers, settings.kafka_topic_features)

    yield

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await consumer.stop()
    await producer.stop()
    await ch_sink.close()
    _pipeline_executor.shutdown(wait=False)


app = FastAPI(title="real-time-risk ml-inference", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health(response: Response) -> dict:
    if _dead_tasks:
        response.status_code = 503
    return {
        "status": "degraded" if _dead_tasks else "ok",
        "dead_background_tasks": sorted(_dead_tasks),
        "models_ready": {d: {"isolation_forest": pipeline.iso_forests[d].ready, "autoencoder": pipeline.autoencoders[d].ready, "xgboost": pipeline.xgboost_detectors[d].ready} for d in DOMAINS},
    }


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")
