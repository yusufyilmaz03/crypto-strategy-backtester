# kripto_bot/net.py
"""HTTPS requests with a CA bundle that works everywhere.

Python from python.org on macOS doesn't use the system certificates until
"Install Certificates.command" is run, so plain urllib fails with
CERTIFICATE_VERIFY_FAILED. certifi (installed with ccxt) ships the bundle.
"""
import ssl
import urllib.request

try:
    import certifi
    CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:      # fall back to the system store
    CONTEXT = ssl.create_default_context()


def urlopen(url, data=None, timeout=30):
    return urllib.request.urlopen(url, data=data, timeout=timeout, context=CONTEXT)
