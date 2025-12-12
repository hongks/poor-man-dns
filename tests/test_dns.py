"""
Test suite for DNS server functionality.

This module contains comprehensive tests for the DNS server implementation,
including unit tests, integration tests, and performance benchmarks.
"""

import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import pytest
import socket
import time
import dns.message
import dns.rdatatype
import dns.rcode
from unittest.mock import AsyncMock, MagicMock

from app.helpers.dns import DNSHandler, DNSServer


# ------------------- TEST CONSTANTS ----------------------

TEST_CUSTOM_DOMAINS = {
    "example.com.": "1.2.3.4",
    "test.local.": "9.9.9.9",
    "ipv6.local.": "2001:db8::1",
}

TEST_BLOCKED_DOMAINS = {"blocked.com.", "ads.example.com."}

TEST_DOH_SERVERS = [
    "https://doh1.example.com/dns-query",
    "https://doh2.example.com/dns-query",
]

# ------------------- FIXTURES ----------------------


@pytest.fixture
def mock_server():
    """Create a mock DNS server with pre-configured responses."""
    server = MagicMock()

    # Basic configuration
    server.dns_custom = TEST_CUSTOM_DOMAINS.copy()
    server.cache_enable = True
    server.target_doh = TEST_DOH_SERVERS.copy()
    server.last_target_doh = None
    server.target_mode = "dns-json"

    # Mock ads blocking
    server.adsblock.get_blocked_domains.return_value = TEST_BLOCKED_DOMAINS
    server.adsblock.get = AsyncMock(return_value=None)
    server.adsblock.get_or_set = AsyncMock(side_effect=lambda key, func: func())
    server.adsblock.set = MagicMock()

    # Mock database operations
    server.sqlite.update = MagicMock()

    # Mock HTTP client
    server.http_client = AsyncMock()
    server.http_client.get.return_value.json.return_value = {
        "Answer": [{"name": "example.com.", "type": 1, "TTL": 300, "data": "1.2.3.4"}]
    }

    return server


@pytest.fixture
def dns_handler(mock_server):
    """Create a DNS handler with mocked transport."""
    handler = DNSHandler(mock_server)
    handler.transport = MagicMock()
    return handler


# ------------------- UNIT TESTS: Custom DNS ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain,record_type,expected_ip",
    [
        ("example.com.", "A", "1.2.3.4"),
        ("test.local.", "A", "9.9.9.9"),
        ("ipv6.local.", "AAAA", "2001:db8::1"),
    ],
)
async def test_handle_request_custom_dns(dns_handler, domain, record_type, expected_ip):
    """Test handling of custom DNS records."""
    q = dns.message.make_query(domain, record_type)
    data = q.to_wire()
    await dns_handler.handle_request(data, ("127.0.0.1", 12345))

    dns_handler.transport.sendto.assert_called_once()
    response = dns.message.from_wire(dns_handler.transport.sendto.call_args[0][0])
    assert response.rcode() == dns.rcode.NOERROR
    assert str(response.answer[0].items[0]) == expected_ip


# ------------------- UNIT TESTS: Blocked Domains ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", TEST_BLOCKED_DOMAINS)
async def test_handle_request_blocked_domain(dns_handler, domain):
    """Test handling of blocked domains."""
    q = dns.message.make_query(domain, "A")
    data = q.to_wire()
    await dns_handler.handle_request(data, ("127.0.0.1", 12345))

    dns_handler.transport.sendto.assert_called_once()
    response = dns.message.from_wire(dns_handler.transport.sendto.call_args[0][0])
    assert response.rcode() == dns.rcode.NXDOMAIN


# ------------------- UNIT TESTS: Error Handling ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_data",
    [
        b"invalid",
        b"\x00" * 12,  # Invalid DNS header
        b"\x00\x00\x01",  # Truncated data
    ],
)
async def test_handle_request_invalid_query(dns_handler, invalid_data):
    """Test handling of invalid DNS queries."""
    await dns_handler.handle_request(invalid_data, ("127.0.0.1", 12345))

    dns_handler.transport.sendto.assert_called_once()
    response = dns.message.from_wire(dns_handler.transport.sendto.call_args[0][0])
    assert response.rcode() == dns.rcode.FORMERR


@pytest.mark.asyncio
async def test_handle_request_doh_server_error(dns_handler):
    """Test handling of DoH server errors."""
    dns_handler.server.http_client.get.side_effect = Exception("Server error")

    q = dns.message.make_query("example.org", "A")
    data = q.to_wire()
    await dns_handler.handle_request(data, ("127.0.0.1", 12345))

    dns_handler.transport.sendto.assert_called_once()
    response = dns.message.from_wire(dns_handler.transport.sendto.call_args[0][0])
    assert response.rcode() == dns.rcode.SERVFAIL


# ------------------- UNIT TESTS: Cache ----------------------


@pytest.mark.asyncio
async def test_handle_request_cache_hit(dns_handler):
    """Test handling of cached DNS responses."""
    cached_response = {
        "response": [dns.rrset.from_text("example.com.", 300, "IN", "A", "1.2.3.4")],
        "timestamp": time.time(),
    }
    dns_handler.server.adsblock.get.return_value = cached_response

    q = dns.message.make_query("example.com", "A")
    data = q.to_wire()
    await dns_handler.handle_request(data, ("127.0.0.1", 12345))

    dns_handler.transport.sendto.assert_called_once()
    response = dns.message.from_wire(dns_handler.transport.sendto.call_args[0][0])
    assert response.rcode() == dns.rcode.NOERROR
    assert str(response.answer[0].items[0]) == "1.2.3.4"
    assert not dns_handler.server.http_client.get.called  # Should not query DoH server


# ------------------- UNIT TESTS: Performance ----------------------


def test_handle_request_performance(benchmark, dns_handler):
    """Test DNS request handling performance."""
    q = dns.message.make_query("example.com.", "A")
    data = q.to_wire()

    async def run():
        await dns_handler.handle_request(data, ("127.0.0.1", 12345))

    result = benchmark(lambda: asyncio.run(run()))
    assert result < 0.1  # Should complete in under 100ms


# ------------------- INTEGRATION TEST FIXTURES ----------------------


class TestConfig:
    """Test configuration for DNS server."""

    class Cache:
        enable = True

    class DNS:
        hostname = "127.0.0.1"
        port = 0  # OS picks a free port
        custom = {}
        target_doh = ["https://fake-doh.local/dns-query"]
        target_mode = "dns-json"

    def __init__(self):
        self.cache = self.Cache()
        self.dns = self.DNS()


@pytest.fixture
async def udp_server():
    """
    Create a DNS server instance for integration testing.

    Returns:
        Tuple[DNSServer, int, asyncio.DatagramTransport]: Server instance, port, and transport
    """
    config = TestConfig()

    # Mock SQLite database
    sqlite = MagicMock()
    sqlite.Session.return_value = MagicMock()

    # Mock ad blocking component
    adsblock = MagicMock()
    adsblock.get_blocked_domains.return_value = TEST_BLOCKED_DOMAINS
    adsblock.get = AsyncMock(return_value=None)
    adsblock.get_or_set = AsyncMock(side_effect=lambda key, func: func())
    adsblock.set = MagicMock()

    # Initialize server
    server = DNSServer(config, sqlite, adsblock)
    server.dns_custom.update(TEST_CUSTOM_DOMAINS)

    # Mock HTTP client for DoH requests
    async def fake_doh_response(url, headers=None, params=None):
        mock_resp = MagicMock()
        domain = params.get("name", "") if params else ""
        mock_resp.json.return_value = {
            "Answer": [{"name": domain, "type": 1, "TTL": 300, "data": "8.8.8.8"}]
        }
        return mock_resp

    server.http_client.get = fake_doh_response

    # Create UDP endpoint
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DNSHandler(server),
        local_addr=(config.dns.hostname, 0),
    )
    server.transport = transport
    port = transport.get_extra_info("sockname")[1]

    yield server, port, transport

    # Cleanup
    transport.close()


@pytest.fixture
def udp_client():
    """Create a UDP client socket for testing."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    yield sock
    sock.close()


# ------------------- INTEGRATION TESTS: Basic Functionality ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain,record_type,expected_ip",
    [
        ("example.com.", "A", "1.2.3.4"),
        ("test.local.", "A", "9.9.9.9"),
        ("ipv6.local.", "AAAA", "2001:db8::1"),
    ],
)
async def test_udp_custom_dns(udp_server, udp_client, domain, record_type, expected_ip):
    """Test resolution of custom DNS entries over UDP."""
    server, port, _ = udp_server

    q = dns.message.make_query(domain, record_type)
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.NOERROR
    assert str(response.answer[0].items[0]) == expected_ip


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", TEST_BLOCKED_DOMAINS)
async def test_udp_blocked_domain(udp_server, udp_client, domain):
    """Test blocking of blacklisted domains."""
    server, port, _ = udp_server

    q = dns.message.make_query(domain, "A")
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.NXDOMAIN


@pytest.mark.asyncio
async def test_udp_forwarded_query(udp_server, udp_client):
    """Test forwarding of DNS queries to DoH servers."""
    server, port, _ = udp_server
    test_domain = "forwarded.example.com."

    q = dns.message.make_query(test_domain, "A")
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.NOERROR
    assert str(response.answer[0].items[0]) == "8.8.8.8"


# ------------------- INTEGRATION TESTS: Error Handling ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_data",
    [
        b"\x01\x02\x03\x04",  # Invalid DNS message
        b"\x00" * 12,  # Invalid header
        b"",  # Empty message
    ],
)
async def test_udp_malformed_packet(udp_server, udp_client, invalid_data):
    """Test handling of malformed DNS packets."""
    _, port, _ = udp_server

    udp_client.sendto(invalid_data, ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.FORMERR


@pytest.mark.asyncio
async def test_udp_doh_server_error(udp_server, udp_client):
    """Test handling of DoH server errors."""
    server, port, _ = udp_server
    server.http_client.get.side_effect = Exception("DoH server error")

    q = dns.message.make_query("example.org", "A")
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.SERVFAIL


# ------------------- INTEGRATION TESTS: Cache ----------------------


@pytest.mark.asyncio
async def test_udp_cache_functionality(udp_server, udp_client):
    """Test DNS response caching."""
    server, port, _ = udp_server
    test_domain = "cache-test.example.com."

    # First query - should go to DoH server
    q = dns.message.make_query(test_domain, "A")
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    # Reset the DoH mock to verify cache hit
    server.http_client.get.reset_mock()

    # Second query - should hit cache
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(512)

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.NOERROR
    assert not server.http_client.get.called  # Verify no DoH request was made


# ------------------- INTEGRATION TESTS: Load Testing ----------------------


@pytest.mark.asyncio
async def test_concurrent_queries(udp_server, udp_client):
    """Test server handling of concurrent DNS queries."""
    server, port, _ = udp_server
    test_domains = [f"test{i}.example.com." for i in range(10)]

    async def send_query(domain):
        q = dns.message.make_query(domain, "A")
        udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
        data, _ = udp_client.recvfrom(512)
        return dns.message.from_wire(data)

    # Send multiple queries concurrently
    tasks = [send_query(domain) for domain in test_domains]
    responses = await asyncio.gather(*tasks)

    # Verify all responses were successful
    for response in responses:
        assert response.rcode() == dns.rcode.NOERROR


@pytest.mark.asyncio
async def test_rapid_sequential_queries(udp_server, udp_client):
    """Test server handling of rapid sequential queries."""
    server, port, _ = udp_server
    query_count = 50
    domain = "rapid.example.com."

    q = dns.message.make_query(domain, "A")
    start_time = time.time()

    for _ in range(query_count):
        udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
        data, _ = udp_client.recvfrom(512)
        response = dns.message.from_wire(data)
        assert response.rcode() == dns.rcode.NOERROR

    end_time = time.time()
    duration = end_time - start_time

    # Verify performance (average response time should be reasonable)
    avg_response_time = duration / query_count
    assert avg_response_time < 0.1  # 100ms per query is a reasonable threshold


@pytest.mark.asyncio
async def test_large_packet_handling(udp_server, udp_client):
    """Test server handling of queries that result in large responses."""
    server, port, _ = udp_server

    # Configure mock to return multiple records
    async def fake_get(url, headers=None, params=None):
        mock_resp = MagicMock()
        # Generate multiple A records
        mock_resp.json.return_value = {
            "Answer": [
                {
                    "name": "large.example.com.",
                    "type": 1,
                    "TTL": 300,
                    "data": f"192.0.2.{i}",
                }
                for i in range(20)  # Multiple A records
            ]
        }
        return mock_resp

    server.http_client.get = fake_get

    q = dns.message.make_query("large.example.com.", "A")
    udp_client.sendto(q.to_wire(), ("127.0.0.1", port))
    data, _ = udp_client.recvfrom(4096)  # Increased buffer size

    response = dns.message.from_wire(data)
    assert response.rcode() == dns.rcode.NOERROR
    assert len(response.answer[0].items) == 20


# ------------------- End of Tests ----------------------


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
    assert (
        result > 50
    ), f"Queries/sec too low for {record_type} with {query_count} queries"
