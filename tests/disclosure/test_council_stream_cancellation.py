"""Cancellation releases a real HTTP reader without waiting for the peer."""
from __future__ import annotations

import http.client
import socket
import threading
from contextlib import suppress

from core.council.dispatch import _interrupt_seat_stream


def test_stop_interrupts_a_reader_waiting_for_the_next_chunk():
    client, peer = socket.socketpair()
    response = http.client.HTTPResponse(client)
    reader_finished = threading.Event()
    stop_finished = threading.Event()
    reader = stopper = None
    try:
        peer.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n")
        response.begin()
        assert response.read(1) == b"x"

        def read():
            try:
                response.read(1)
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                response.close()
                reader_finished.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        assert not reader_finished.wait(.05)

        def stop():
            try:
                _interrupt_seat_stream(response)
            finally:
                stop_finished.set()

        stopper = threading.Thread(target=stop, daemon=True)
        stopper.start()
        assert stop_finished.wait(2), "Stop waited for the peer instead of interrupting the stream"
        assert reader_finished.wait(2), "The cancelled reader is still running"
        _interrupt_seat_stream(response)
    finally:
        with suppress(OSError):
            peer.shutdown(socket.SHUT_RDWR)
        peer.close()
        if reader is not None:
            reader.join(2)
        if stopper is not None:
            stopper.join(2)
        response.close()
        client.close()
