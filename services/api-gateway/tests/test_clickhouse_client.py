import httpx
import pytest

from app.clickhouse_client import ClickHouseClient


def make_client(handler) -> ClickHouseClient:
    client = ClickHouseClient("http://clickhouse:8123", "risk", "default", "secret")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_query_rows_parses_ndjson_response():
    def handler(request: httpx.Request) -> httpx.Response:
        body = '{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n'
        return httpx.Response(200, text=body)

    client = make_client(handler)
    rows = await client.query_rows("SELECT a, b FROM t")
    assert rows == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


@pytest.mark.asyncio
async def test_query_rows_empty_response_returns_empty_list():
    client = make_client(lambda request: httpx.Response(200, text=""))
    rows = await client.query_rows("SELECT 1")
    assert rows == []


@pytest.mark.asyncio
async def test_query_rows_passes_params_with_param_prefix():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, text="")

    client = make_client(handler)
    await client.query_rows("SELECT * FROM t WHERE domain = {domain:String}", {"domain": "market"})
    assert captured["param_domain"] == "market"
    assert captured["database"] == "risk"


@pytest.mark.asyncio
async def test_query_rows_raises_on_http_error():
    client = make_client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.query_rows("SELECT 1")


@pytest.mark.asyncio
async def test_query_rows_sends_basic_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" in {k.lower() for k in request.headers}
        return httpx.Response(200, text="")

    client = make_client(handler)
    await client.query_rows("SELECT 1")
