import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import pytest
import socket
import time

import dns.message
from unittest.mock import AsyncMock, MagicMock

from app.helpers.dns import DNSHandler, DNSServer


# ------------------- UNIT TESTS ----------------------


@pytest.fixture
def mock_server():
    server = MagicMock()
    server.dns_custom = {"example.com.": "1.2.3.4"}
    server.adsblock.get_blocked_domains.return_value = {"blocked.com."}
    server.cache_enable = True
    server.adsblock.get = AsyncMock(return_value=None)
    server.adsblock.get_or_set = AsyncMock(side_effect=lambda key, func: func())
    server.adsblock.set = MagicMock()
    server.target_doh = ["https://doh.example.com/dns-query"]
    server.last_target_doh = None
    server.target_mode = "dns-json"
    server.sqlite.update = MagicMock()
    server.http_client = AsyncMock()
    return server


@pytest.mark.asyncio
async def test_handle_request_custom_dns(mock_server):
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    q = dns.message.make_query("example.com.", "A")
    data = q.to_wire()
    await handler.handle_request(data, ("127.0.0.1", 12345))
    handler.transport.sendto.assert_called()


@pytest.mark.asyncio
async def test_handle_request_blocked_domain(mock_server):
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    q = dns.message.make_query("blocked.com.", "A")
    data = q.to_wire()
    await handler.handle_request(data, ("127.0.0.1", 12345))
    handler.transport.sendto.assert_called()


@pytest.mark.asyncio
async def test_handle_request_invalid_query(mock_server):
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    await handler.handle_request(b"invalid", ("127.0.0.1", 12345))
    handler.transport.sendto.assert_called()


@pytest.mark.asyncio
async def test_handle_request_forward_to_doh(mock_server):
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    q = dns.message.make_query("google.com.", "A")
    data = q.to_wire()
    handler.forward_to_doh = AsyncMock(return_value=dns.message.make_response(q))
    await handler.handle_request(data, ("127.0.0.1", 12345))
    handler.transport.sendto.assert_called()


def test_handle_request_performance(benchmark, mock_server):
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    q = dns.message.make_query("example.com.", "A")
    data = q.to_wire()

    async def run():
        await handler.handle_request(data, ("127.0.0.1", 12345))

    benchmark(lambda: asyncio.run(run()))


# ------------------- UDP FIXTURE ----------------------


@pytest.fixture
async def udp_server():
    """Start a DNSServer with DNSHandler on a random UDP port for integration testing."""

    class Config:
        class Cache:
            enable = True

        cache = Cache()

        class DNS:
            hostname = "127.0.0.1"
            port = 0  # OS picks a free port
            custom = {}
            target_doh = ["https://fake-doh.local/dns-query"]
            target_mode = "dns-json"

        dns = DNS()

    sqlite = MagicMock()
    sqlite.Session.return_value = MagicMock()
    adsblock = MagicMock()
    adsblock.get_blocked_domains.return_value = set()
    adsblock.get = AsyncMock(return_value=None)
    adsblock.get_or_set = AsyncMock(side_effect=lambda key, func: func())
    adsblock.set = MagicMock()

    server = DNSServer(Config, sqlite, adsblock)

    # Mock http client to avoid real network calls
    async def fake_get(url, headers=None, params=None):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Answer": []}
        return mock_resp

    server.http_client.get = fake_get

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DNSHandler(server),
        local_addr=(Config.dns.hostname, 0),
    )
    server.transport = transport
    port = transport.get_extra_info("sockname")[1]

    yield server, port, transport

    transport.close()


# ------------------- UDP INTEGRATION TESTS ----------------------


@pytest.mark.asyncio
async def test_udp_custom_dns(udp_server):
    server, port, _ = udp_server
    server.dns_custom["test.local."] = "9.9.9.9"

    q = dns.message.make_query("test.local.", "A")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)

    try:
        sock.sendto(q.to_wire(), ("127.0.0.1", port))
        data, _ = sock.recvfrom(512)
        response = dns.message.from_wire(data)
        assert response.rcode() == dns.rcode.NOERROR
        assert str(response.answer[0].items[0]) == "9.9.9.9"
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_udp_blocked_domain(udp_server):
    server, port, _ = udp_server
    server.adsblock.get_blocked_domains.return_value = {"blocked.local."}

    q = dns.message.make_query("blocked.local.", "A")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)

    try:
        sock.sendto(q.to_wire(), ("127.0.0.1", port))
        data, _ = sock.recvfrom(512)
        response = dns.message.from_wire(data)
        assert response.rcode() == dns.rcode.NXDOMAIN
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_udp_forwarded_query(udp_server):
    server, port, _ = udp_server
    server.dns_custom.clear()
    server.adsblock.get_blocked_domains.return_value = set()

    async def fake_get(url, headers=None, params=None):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Answer": [
                {"name": "forwarded.local.", "type": 1, "TTL": 300, "data": "8.8.8.8"}
            ]
        }
        return mock_resp

    server.http_client.get = fake_get

    q = dns.message.make_query("forwarded.local.", "A")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)

    try:
        sock.sendto(q.to_wire(), ("127.0.0.1", port))
        data, _ = sock.recvfrom(512)
        response = dns.message.from_wire(data)
        assert response.rcode() == dns.rcode.NOERROR
        assert str(response.answer[0].items[0]) == "8.8.8.8"
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_udp_malformed_packet(udp_server):
    _, port, _ = udp_server
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)

    try:
        sock.sendto(b"\x01\x02\x03\x04", ("127.0.0.1", port))
        data, _ = sock.recvfrom(512)
        response = dns.message.from_wire(data)
        assert response.rcode() == dns.rcode.FORMERR
    finally:
        sock.close()


# ------------------- STRESS TEST ----------------------


@pytest.mark.asyncio
async def test_udp_stress_100_queries(udp_server):
    """
    Stress test: send 100 concurrent UDP DNS queries and verify all respond.
    """
    server, port, _ = udp_server
    server.dns_custom["stress.local."] = "7.7.7.7"

    async def send_query():
        q = dns.message.make_query("stress.local.", "A")
        loop = asyncio.get_running_loop()

        def do_query():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            try:
                sock.sendto(q.to_wire(), ("127.0.0.1", port))
                data, _ = sock.recvfrom(512)
                return dns.message.from_wire(data)
            finally:
                sock.close()

        return await loop.run_in_executor(None, do_query)

    tasks = [send_query() for _ in range(100)]
    responses = await asyncio.gather(*tasks)

    for resp in responses:
        assert resp.rcode() == dns.rcode.NOERROR
        assert str(resp.answer[0].items[0]) == "7.7.7.7"


# ------------------- BENCHMARKS ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("query_count", [100, 500, 1000])
@pytest.mark.parametrize("record_type", ["A", "AAAA", "PTR"])
async def test_udp_stress_benchmark_types(
    query_count, record_type, udp_server, benchmark
):
    """
    Benchmark: send multiple DNS queries (A, AAAA, PTR) at different volumes (100/500/1000).
    Prints queries/sec directly for each combination.
    """
    server, port, _ = udp_server
    if record_type == "A":
        server.dns_custom["bench.local."] = "6.6.6.6"
    elif record_type == "AAAA":
        server.dns_custom["bench.local."] = "2001:4860:4860::8888"
    elif record_type == "PTR":
        server.dns_custom["bench.local."] = "ptr.target.local."

    q = dns.message.make_query("bench.local.", record_type)
    loop = asyncio.get_running_loop()

    def do_query():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        try:
            sock.sendto(q.to_wire(), ("127.0.0.1", port))
            data, _ = sock.recvfrom(512)
            return dns.message.from_wire(data)
        finally:
            sock.close()

    def response_answer_matches(rt, resp):
        if rt == "A":
            return str(resp.answer[0].items[0]) == "6.6.6.6"
        elif rt == "AAAA":
            return str(resp.answer[0].items[0]) == "2001:4860:4860::8888"
        elif rt == "PTR":
            return str(resp.answer[0].items[0]) == "ptr.target.local."
        return False

    async def run_queries(n):
        start = time.perf_counter()
        for _ in range(n):
            resp = await loop.run_in_executor(None, do_query)
            assert resp.rcode() == dns.rcode.NOERROR
            assert response_answer_matches(record_type, resp)
        end = time.perf_counter()
        duration = end - start
        qps = n / duration
        print(f"\n[Benchmark] {record_type} x {n} → {qps:.1f} qps in {duration:.3f}s")
        return qps

    result = benchmark(lambda: asyncio.run(run_queries(query_count)))
    assert result > 50, (
        f"Queries/sec too low for {record_type} with {query_count} queries"
    )
