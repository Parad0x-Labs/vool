"""Socket transport for DNS answers already approved by an outbound caller.

Used only inside the canonical effect door. It neither grants network permission
nor decides which addresses are allowed. The original hostname stays on the HTTP
connection for Host, TLS SNI and certificate verification; only the socket address
is replaced. No process-global resolver patch or ambient proxy is involved.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.request


def pinned_http_handlers(addresses: tuple[str, ...], *, context=None):
    approved = tuple(ipaddress.ip_address(address) for address in addresses)
    if not approved:
        raise ValueError("A pinned connection requires at least one approved address")

    def connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        # HTTPConnection supplies its original hostname and port. Never resolve
        # that hostname a second time after the caller's security decision.
        _host, port = address
        last_error = None
        for ip in approved:
            family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                target = (str(ip), port, 0, 0) if ip.version == 6 else (str(ip), port)
                sock.connect(target)
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
            except BaseException:
                sock.close()
                raise
        raise last_error

    class PinnedHTTPConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(PinnedHTTPConnection, request)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(PinnedHTTPSConnection, request, context=self._context)

    return [HTTPHandler(), HTTPSHandler(context=context)]
