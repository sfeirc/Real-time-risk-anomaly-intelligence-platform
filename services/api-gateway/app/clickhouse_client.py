"""Thin async ClickHouse HTTP client for read queries. All query strings in
this service are built from a fixed set of hand-written templates with
parameters bound through ClickHouse's own `{name:Type}` parameterized-query
syntax (never plain string interpolation of request input) — the one thing
that must never regress here is a SQL injection path from a dashboard filter
straight into `risk.alerts`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class ClickHouseClient:
    def __init__(self, base_url: str, database: str, user: str, password: str) -> None:
        self._base_url = base_url
        self._database = database
        self._auth = (user, password)
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def query_rows(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """`query` uses ClickHouse parameter placeholders, e.g. `{domain:String}`;
        `params` supplies the matching Python values."""
        request_params = {
            "database": self._database,
            "query": f"{query} FORMAT JSONEachRow",
            # ClickHouse quotes UInt64/Int64 as JSON strings by default (a JS
            # safe-integer safety net for values that could exceed 2^53).
            # Every one of ours (alert_count, events, events_scored, ...)
            # stays far below that, and the dashboard does arithmetic
            # directly on these fields (`0 += row.events`) — with the
            # default quoting that's string concatenation, not addition,
            # and silently produces a huge garbage number instead of an
            # error. Disabling the quoting here fixes it at the one place
            # that talks to ClickHouse, instead of coercing types in every
            # frontend chart that touches a UInt64 column.
            "output_format_json_quote_64bit_integers": "0",
        }
        for key, value in (params or {}).items():
            request_params[f"param_{key}"] = value

        resp = await self._client.post(f"{self._base_url}/", params=request_params, auth=self._auth)
        resp.raise_for_status()
        return [json.loads(line) for line in resp.text.strip().splitlines() if line]
