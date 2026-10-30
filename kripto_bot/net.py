# kripto_bot/net.py
"""HTTPS requests with a CA bundle that works everywhere, IPv4 first.

Python from python.org on macOS doesn't use the system certificates until
"Install Certificates.command" is run, so plain urllib fails with
CERTIFICATE_VERIFY_FAILED. certifi (installed with ccxt) ships the bundle.

Python also tries a host's addresses in DNS order, IPv6 first, and waits out the
whole timeout on each one that doesn't answer (browsers and curl race them instead).
On a network where IPv6 is advertised but doesn't work, every request to an
IPv6-capable host such as api.telegram.org took the full timeout (15-65 s) before
trying IPv4. Here IPv4 addresses are tried first.
"""
import http.client
import socket
import ssl
import urllib.request

try:
    import certifi
    CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:      # fall back to the system store
    CONTEXT = ssl.create_default_context()


def create_connection(address, *args, getaddrinfo=socket.getaddrinfo, connect=socket.create_connection, **kwargs):
    """socket.create_connection with the IPv4 addresses tried before the IPv6 ones."""
    host, port = address
    infos = sorted(getaddrinfo(host, port, 0, socket.SOCK_STREAM), key=lambda i: i[0] != socket.AF_INET)
    error = OSError(f"no address for {host}")
    for *_, sockaddr in infos:
        try:
            return connect(sockaddr[:2], *args, **kwargs)
        except OSError as e:
            error = e
    raise error


class _Connection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_connection    # TLS still checks the host name


class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Connection, req, context=self._context)


_OPENER = urllib.request.build_opener(_Handler(context=CONTEXT))


def urlopen(url, data=None, timeout=30):
    return _OPENER.open(url, data=data, timeout=timeout)
