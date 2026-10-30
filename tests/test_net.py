import socket

import pytest

from kripto_bot import net

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


def _connect(dead=()):
    tried = []

    def connect(address, *args, **kwargs):
        tried.append(address[0])
        if address[0] in dead:
            raise OSError("timed out")
        return f"socket to {address[0]}"

    return connect, tried


def test_ipv4_is_tried_before_ipv6():
    connect, tried = _connect()
    sock = net.create_connection(("api.example", 443), 15, getaddrinfo=lambda *a: [V6, V4], connect=connect)
    assert sock == "socket to 192.0.2.1" and tried == ["192.0.2.1"]


def test_ipv6_is_still_used_when_ipv4_fails():
    connect, tried = _connect(dead={"192.0.2.1"})
    sock = net.create_connection(("api.example", 443), 15, getaddrinfo=lambda *a: [V6, V4], connect=connect)
    assert sock == "socket to 2001:db8::1" and tried == ["192.0.2.1", "2001:db8::1"]


def test_last_error_is_raised():
    connect, _ = _connect(dead={"192.0.2.1", "2001:db8::1"})
    with pytest.raises(OSError, match="timed out"):
        net.create_connection(("api.example", 443), 15, getaddrinfo=lambda *a: [V6, V4], connect=connect)
