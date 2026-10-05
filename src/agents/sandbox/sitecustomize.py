"""Sandbox security customizer injected via PYTHONPATH into subprocess test commands.

Blocks outbound network access at the Python runtime level:
- socket.socket.connect (except loopback/localhost)
- socket.create_connection (except loopback/localhost)
- http.client.HTTPConnection / HTTPSConnection (except loopback/localhost)
- urllib.request.urlopen (except loopback/localhost)

Emits a clear, actionable error:
"[blocked] network access is disabled in this sandbox"
"""
from __future__ import annotations

import ipaddress
import socket
import sys

_orig_socket_connect = socket.socket.connect
_orig_create_connection = socket.create_connection

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback(host: str) -> bool:
    if not host or not isinstance(host, str):
        return False
    host_clean = host.strip().lower()
    if host_clean in LOOPBACK_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host_clean)
        return ip.is_loopback
    except ValueError:
        return False


def _extract_host(address) -> str:
    if isinstance(address, tuple) and len(address) >= 1:
        return str(address[0])
    if isinstance(address, str):
        return address
    return ""


def _guard_socket_connect(self, address, *args, **kwargs):
    host = _extract_host(address)
    if not _is_loopback(host):
        raise PermissionError(
            f"[blocked] network access is disabled in this sandbox (target: {host})"
        )
    return _orig_socket_connect(self, address, *args, **kwargs)


def _guard_create_connection(address, *args, **kwargs):
    host = _extract_host(address)
    if not _is_loopback(host):
        raise PermissionError(
            f"[blocked] network access is disabled in this sandbox (target: {host})"
        )
    return _orig_create_connection(address, *args, **kwargs)


socket.socket.connect = _guard_socket_connect
socket.create_connection = _guard_create_connection

# Patch http.client
try:
    import http.client

    _orig_http_connect = http.client.HTTPConnection.connect

    def _guard_http_connect(self):
        if not _is_loopback(self.host):
            raise PermissionError(
                f"[blocked] network access is disabled in this sandbox (target: {self.host})"
            )
        return _orig_http_connect(self)

    http.client.HTTPConnection.connect = _guard_http_connect

    _orig_https_connect = getattr(http.client, "HTTPSConnection", None)
    if _orig_https_connect and hasattr(_orig_https_connect, "connect"):
        orig_s_connect = _orig_https_connect.connect

        def _guard_https_connect(self):
            if not _is_loopback(self.host):
                raise PermissionError(
                    f"[blocked] network access is disabled in this sandbox (target: {self.host})"
                )
            return orig_s_connect(self)

        http.client.HTTPSConnection.connect = _guard_https_connect
except ImportError:
    pass

# Patch urllib.request
try:
    import urllib.request

    _orig_urlopen = urllib.request.urlopen

    def _guard_urlopen(url, *args, **kwargs):
        req_host = ""
        if isinstance(url, str):
            from urllib.parse import urlparse

            parsed = urlparse(url)
            req_host = parsed.hostname or ""
        elif hasattr(url, "host"):
            req_host = getattr(url, "host") or ""
        elif hasattr(url, "full_url"):
            from urllib.parse import urlparse

            req_host = urlparse(url.full_url).hostname or ""

        if not _is_loopback(req_host):
            raise PermissionError(
                f"[blocked] network access is disabled in this sandbox (target: {req_host or url})"
            )
        return _orig_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = _guard_urlopen
except ImportError:
    pass
