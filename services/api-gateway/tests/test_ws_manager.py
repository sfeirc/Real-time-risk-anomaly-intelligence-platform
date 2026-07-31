import pytest

from app.ws_manager import WebSocketManager


class FakeWebSocket:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_connect_accepts_and_tracks_client():
    mgr = WebSocketManager()
    ws = FakeWebSocket()
    await mgr.connect(ws)
    assert ws.accepted
    assert mgr.client_count == 1


@pytest.mark.asyncio
async def test_disconnect_removes_client():
    mgr = WebSocketManager()
    ws = FakeWebSocket()
    await mgr.connect(ws)
    mgr.disconnect(ws)
    assert mgr.client_count == 0


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_clients():
    mgr = WebSocketManager()
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    await mgr.connect(ws1)
    await mgr.connect(ws2)
    await mgr.broadcast({"type": "alert", "data": {"x": 1}})
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert '"type": "alert"' in ws1.sent[0]


@pytest.mark.asyncio
async def test_broadcast_drops_dead_clients_without_raising():
    mgr = WebSocketManager()
    good, bad = FakeWebSocket(), FakeWebSocket(fail=True)
    await mgr.connect(good)
    await mgr.connect(bad)
    await mgr.broadcast({"type": "alert"})
    assert len(good.sent) == 1
    assert mgr.client_count == 1  # bad client evicted


@pytest.mark.asyncio
async def test_broadcast_with_no_clients_is_a_noop():
    mgr = WebSocketManager()
    await mgr.broadcast({"type": "alert"})  # must not raise
    assert mgr.client_count == 0
