"""Sperre eines nicht erreichbaren KI-Rechners im Worker (timeout_at / is_down / TCP-Test)."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.worker.worker import AIServer, Worker


def _server(timeout_at=None, ip="127.0.0.1", port="1"):
    return AIServer(id=1, name="A", ip_address=ip, port=port, parallel_request=2, active=True, timeout_at=timeout_at)


def test_is_down_only_while_block_is_in_the_future():
    now = datetime.now(timezone.utc)
    assert not _server(None).is_down
    assert _server(now + timedelta(seconds=60)).is_down
    assert not _server(now - timedelta(seconds=1)).is_down               # abgelaufen → wieder versuchen
    assert _server(datetime.utcnow() + timedelta(seconds=60)).is_down     # naive Zeit wird als UTC gelesen


def test_unreachable_server_is_detected_and_reachable_one_is_not():
    async def run():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            assert await Worker._server_reachable(_server(port=str(port)))
        finally:
            server.close()
            await server.wait_closed()
        assert not await Worker._server_reachable(_server(port=str(port)))   # Port jetzt geschlossen

    asyncio.run(run())
