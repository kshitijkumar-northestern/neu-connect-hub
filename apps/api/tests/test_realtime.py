import pytest
import socketio


async def connect(url: str, origin: str) -> socketio.AsyncClient:
    # WebSocket closes immediately on disconnect; long-polling would hold a request open
    # for the full ping interval (~25 s). Both transports go through the same origin check.
    client = socketio.AsyncClient(reconnection=False)
    await client.connect(url, headers={"Origin": origin}, transports=["websocket"], wait_timeout=5)
    return client


async def test_a_client_from_the_web_origin_can_connect(live_server: str) -> None:
    client = await connect(live_server, "http://localhost:8080")
    try:
        assert client.connected
    finally:
        await client.disconnect()


async def test_a_client_from_another_origin_is_refused(live_server: str) -> None:
    # Same client, same server: only the Origin differs, so a refusal can only come from the
    # origin check (and not, say, from a missing HTTP transport).
    allowed = await connect(live_server, "http://localhost:8080")
    await allowed.disconnect()

    with pytest.raises(socketio.exceptions.ConnectionError):
        await connect(live_server, "http://evil.example")
