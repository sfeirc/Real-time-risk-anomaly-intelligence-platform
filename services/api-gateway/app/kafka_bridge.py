"""Bridges Kafka `alerts` / `model-metrics` topics to connected WebSocket
clients. api-gateway is a second, independent consumer group on both
topics — it never affects ml-inference's own consumption, and a slow/absent
dashboard client never applies backpressure to the scoring pipeline.
"""

from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaConsumer

from .ws_manager import WebSocketManager

log = logging.getLogger("api-gateway.kafka_bridge")


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
            await ws_manager.broadcast({"type": message_type, "data": msg.value})
            counter.inc()
    finally:
        await consumer.stop()
