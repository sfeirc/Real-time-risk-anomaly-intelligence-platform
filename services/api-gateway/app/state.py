"""Shared singletons, populated during FastAPI's lifespan startup (they need
a running event loop) and read by route modules. A single-process gateway
with one ClickHouse client / one httpx client / one WS registry doesn't need
anything fancier than module-level references — see main.py for the
lifespan wiring that sets these.
"""

from __future__ import annotations

import httpx

from .clickhouse_client import ClickHouseClient
from .ws_manager import WebSocketManager

clickhouse: ClickHouseClient | None = None
http_client: httpx.AsyncClient | None = None
ws_manager = WebSocketManager()
