import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import base64
import pytest

import dns.message
from unittest.mock import AsyncMock, MagicMock, patch

from app.helpers.doh import DOHHandler, DOHServer


@pytest.fixture
def mock_server():
    server = MagicMock()
    server.http_client = AsyncMock()
    server.sqlite.update = MagicMock()

    server.cache_enable = True
    server.dns_custom = {"example.com.": "1.2.3.4"}
    server.filepath = "/tmp"
    server.last_target_doh = None
    server.target_doh = ["https://doh.example.com/dns-query"]
    server.target_mode = "dns-message"

    server.adsblock.get_blocked_domains.return_value = set(["blocked.com."])
    server.adsblock.get_or_set = AsyncMock(side_effect=lambda key, func: func())
    server.adsblock.get = AsyncMock(return_value=None)
    server.adsblock.set = MagicMock()
    return server


# ─────────────────────────────
# DOHHandler Tests
# ─────────────────────────────


@pytest.mark.asyncio
async def test_handle_query_custom_dns(mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("example.com.", "A")
    data = q.to_wire()
    response = await handler.handle_query("127.0.0.1", data)
    assert response.status == 200
    assert response.content_type == "application/dns-message"


@pytest.mark.asyncio
async def test_handle_query_blocked_domain(mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("blocked.com.", "A")
    data = q.to_wire()
    response = await handler.handle_query("127.0.0.1", data)
    assert response.status == 200
    assert response.content_type == "application/dns-message"


@pytest.mark.asyncio
async def test_handle_query_invalid_query(mock_server):
    handler = DOHHandler(mock_server)
    response = await handler.handle_query("127.0.0.1", b"invalid")
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_query_forward_to_doh(mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("google.com.", "A")
    data = q.to_wire()
    handler.forward_to_doh = AsyncMock(return_value="FORWARDED")
    result = await handler.handle_query("127.0.0.1", data)
    assert result == "FORWARDED"


@pytest.mark.asyncio
async def test_handle_query_cache_hit(mock_server):
    """Simulates a cache hit to avoid calling forward_to_doh."""
    handler = DOHHandler(mock_server)
    cached_response = dns.message.make_response(
        dns.message.make_query("cached.com.", "A")
    )
    mock_server.adsblock.get.return_value = {"response": cached_response.answer}
    q = dns.message.make_query("cached.com.", "A")
    data = q.to_wire()

    response = await handler.handle_query("127.0.0.1", data)
    assert response.status == 200
    assert b"cached.com" in response.body


@pytest.mark.asyncio
async def test_forward_to_doh_dns_message_success(mock_server):
    handler = DOHHandler(mock_server)

    mock_resp = MagicMock()
    mock_resp.content = dns.message.make_response(
        dns.message.make_query("test.com.", "A")
    ).to_wire()
    mock_resp.raise_for_status = MagicMock()

    mock_server.http_client.post = AsyncMock(return_value=mock_resp)

    q = dns.message.make_query("test.com.", "A")
    resp = await handler.forward_to_doh("127.0.0.1", q, "test.com.", "A", "test.com.:A")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_forward_to_doh_http_error(mock_server):
    handler = DOHHandler(mock_server)
    mock_server.http_client.post = AsyncMock(side_effect=Exception("boom"))

    q = dns.message.make_query("fail.com.", "A")
    resp = await handler.forward_to_doh("127.0.0.1", q, "fail.com.", "A", "fail.com.:A")
    assert resp.status == 500


@pytest.mark.asyncio
async def test_do_get_valid_request(mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("example.com.", "A").to_wire()
    encoded = base64.urlsafe_b64encode(q).decode().rstrip("=")

    req = MagicMock()
    req.text = AsyncMock(return_value="")
    req.query = {"dns": encoded}
    req.remote = "127.0.0.1"

    resp = await handler.do_GET(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_do_get_missing_dns_param(mock_server):
    handler = DOHHandler(mock_server)
    req = MagicMock()
    req.text = AsyncMock(return_value="")
    req.query = {}
    req.remote = "127.0.0.1"

    resp = await handler.do_GET(req)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_do_post_valid(mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("example.com.", "A").to_wire()

    req = MagicMock()
    req.read = AsyncMock(return_value=q)
    req.content_type = "application/dns-message"
    req.remote = "127.0.0.1"

    resp = await handler.do_POST(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_do_post_invalid_content_type(mock_server):
    handler = DOHHandler(mock_server)
    req = MagicMock()
    req.read = AsyncMock(return_value=b"data")
    req.content_type = "text/plain"
    req.remote = "127.0.0.1"

    resp = await handler.do_POST(req)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_forward_to_doh_sets_cache(mock_server):
    """Ensure forward_to_doh calls adsblock.set() when cache is enabled."""
    handler = DOHHandler(mock_server)

    # Mock httpx.post to return a successful DNS response
    mock_resp = MagicMock()
    mock_resp.content = dns.message.make_response(
        dns.message.make_query("cacheme.com.", "A")
    ).to_wire()
    mock_resp.raise_for_status = MagicMock()

    mock_server.http_client.post = AsyncMock(return_value=mock_resp)

    q = dns.message.make_query("cacheme.com.", "A")
    await handler.forward_to_doh("127.0.0.1", q, "cacheme.com.", "A", "cacheme.com.:A")

    # adsblock.set() should have been called exactly once
    mock_server.adsblock.set.assert_called_once()
    args, kwargs = mock_server.adsblock.set.call_args
    assert args[0] == "cacheme.com.:A"  # cache key
    assert "response" in args[1]  # cached response should include DNS answer


# ─────────────────────────────
# DOHServer Tests
# ─────────────────────────────


@pytest.fixture
def mock_config():
    class Dummy:
        class Cache:
            enable = True

        class DoH:
            hostname = "127.0.0.1"
            port = 5053

        class DNS:
            custom = {"local.lan.": "192.168.1.1"}
            target_doh = ["https://doh.example.com/dns-query"]
            target_mode = "dns-message"

        cache = Cache()
        doh = DoH()
        dns = DNS()
        filepath = "/tmp"

    return Dummy()


@pytest.mark.asyncio
@patch("ssl.create_default_context")
async def test_dohserver_listen_and_close(mock_ssl, mock_config):
    """Tests listen() and close() without spinning a real TLS server."""
    mock_ssl.return_value = MagicMock()
    server = DOHServer(mock_config, sqlite=MagicMock(), adsblock=MagicMock())

    # Patch aiohttp parts
    with (
        patch("app.helpers.doh.web.AppRunner") as mock_runner,
        patch("app.helpers.doh.web.TCPSite") as mock_site,
    ):
        mock_runner_instance = AsyncMock()
        mock_site_instance = AsyncMock()

        mock_runner.return_value = mock_runner_instance
        mock_site.return_value = mock_site_instance

        # Run listen() in a task and cancel after 0.1s to avoid infinite sleep
        async def run_listen():
            task = asyncio.create_task(server.listen())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_listen()
        await server.close()

        mock_runner_instance.setup.assert_awaited()
        mock_site_instance.start.assert_awaited()


@pytest.mark.asyncio
@patch("ssl.create_default_context")
async def test_dohserver_reload(mock_ssl, mock_config):
    """Ensure reload() calls close() and listen()."""
    mock_ssl.return_value = MagicMock()
    server = DOHServer(mock_config, sqlite=MagicMock(), adsblock=MagicMock())
    server.listen = AsyncMock()
    server.close = AsyncMock()

    await server.reload(mock_config, MagicMock())
    server.close.assert_awaited()
    server.listen.assert_awaited()


# ─────────────────────────────
# Performance Test
# ─────────────────────────────
def test_handle_query_performance(benchmark, mock_server):
    handler = DOHHandler(mock_server)
    q = dns.message.make_query("example.com.", "A")
    data = q.to_wire()

    async def run():
        await handler.handle_query("127.0.0.1", data)

    benchmark(lambda: asyncio.run(run()))
