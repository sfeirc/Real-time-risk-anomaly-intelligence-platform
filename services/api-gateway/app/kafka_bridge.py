"""Bridges Kafka `alerts` / `model-metrics` topics to connected WebSocket
clients. api-gateway is a second, independent consumer group on both
topics — it never affects ml-inference's own consumption, and a slow/absent
dashboard client never applies backpressure to the scoring pipeline.
"""

from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaConsumer
from opentelemetry import trace

from . import telemetry
from .ws_manager import WebSocketManager

log = logging.getLogger("api-gateway.kafka_bridge")
tracer = trace.get_tracer("api-gateway")


async def relay_topic(brokers: str, topic: str, group_id: str, message_type: str, ws_manager: WebSocketManager, counter) -> None:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=brokers,
        group_id=group_id,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    await consumer.start()
    log.info("relaying topic=%s as type=%s", topic, message_type)
    try:
        async for msg in consumer:
            # Closes the trace ml-inference continued from feature-service
            # (see app/main.py's score_window span): api-gateway is the last
            # hop, so this relay-to-WS span is where a WS-ingest event's
            # whole trace ends, in Jaeger. Only alert messages carry a
            # traceparent (see ml-inference's inject_current_context) -
            # model-metrics is a periodic aggregate, not a per-event trace.
            parent_cx = telemetry.extract_parent_context(msg.headers) if message_type == "alert" else None
            with tracer.start_as_current_span("relay_to_ws", context=parent_cx):
                await ws_manager.broadcast({"type": message_type, "data": msg.value})
                counter.inc()
    finally:
        await consumer.stop()
