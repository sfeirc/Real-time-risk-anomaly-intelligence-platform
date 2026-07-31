from __future__ import annotations

import json

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer


async def make_consumer(brokers: str, topic: str, group_id: str) -> AIOKafkaConsumer:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=brokers,
        group_id=group_id,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    await consumer.start()
    return consumer


async def make_producer(brokers: str) -> AIOKafkaProducer:
    producer = AIOKafkaProducer(
        bootstrap_servers=brokers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k is not None else None,
        compression_type="lz4",
        linger_ms=5,
    )
    await producer.start()
    return producer
