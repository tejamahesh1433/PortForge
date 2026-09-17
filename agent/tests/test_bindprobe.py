"""Tests for the real socket bind-availability probe.

These use real sockets (loopback / an occupied ephemeral port), not mocks
-- the whole point of this probe is that it's a real OS-level check, so
mocking the socket module would test nothing meaningful.
"""
import socket

import pytest

from portforge_agent.bindprobe import probe_bind
from portforge_agent.models import Protocol


def _free_tcp_port() -> int:
    """Ask the OS for a genuinely free ephemeral port (bind to port 0)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_free_tcp_port_is_available():
    port = _free_tcp_port()
    result = probe_bind(port, Protocol.TCP, "127.0.0.1")
    assert result.available is True
    assert result.reason is None


def test_occupied_tcp_port_is_unavailable():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]

        result = probe_bind(port, Protocol.TCP, "127.0.0.1")
        assert result.available is False
        assert result.reason is not None


def test_probe_closes_socket_after_success(monkeypatch):
    port = _free_tcp_port()
    closed = []

    original_socket = socket.socket

    class _TrackingSocket(original_socket):
        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(socket, "socket", _TrackingSocket)
    result = probe_bind(port, Protocol.TCP, "127.0.0.1")
    assert result.available is True
    assert closed == [True]


def test_probe_closes_socket_after_failure(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]

        closed = []
        original_socket = socket.socket

        class _TrackingSocket(original_socket):
            def close(self):
                closed.append(True)
                super().close()

        monkeypatch.setattr(socket, "socket", _TrackingSocket)
        result = probe_bind(port, Protocol.TCP, "127.0.0.1")
        assert result.available is False
        assert closed == [True]


def test_free_udp_port_is_available():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # s is now closed -- port should be free again (best-effort on a fast
    # machine; UDP has no TIME_WAIT the way TCP does).
    result = probe_bind(port, Protocol.UDP, "127.0.0.1")
    assert result.available is True


def test_occupied_udp_port_is_unavailable():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]

        result = probe_bind(port, Protocol.UDP, "127.0.0.1")
        assert result.available is False


def test_ipv4_address_selects_af_inet(monkeypatch):
    captured = {}
    original_socket = socket.socket

    def _tracking(family, type_, *a, **kw):
        captured["family"] = family
        return original_socket(family, type_, *a, **kw)

    monkeypatch.setattr(socket, "socket", _tracking)
    probe_bind(_free_tcp_port(), Protocol.TCP, "127.0.0.1")
    assert captured["family"] == socket.AF_INET


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available on this system")
def test_ipv6_address_selects_af_inet6():
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        try:
            s.bind(("::1", 0))
        except OSError:
            pytest.skip("IPv6 loopback not usable in this environment")
        port = s.getsockname()[1]

    result = probe_bind(port, Protocol.TCP, "::1")
    assert result.available is True  # port is free again now that s is closed


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available on this system")
def test_ipv6_occupied_port_is_unavailable():
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as holder:
        try:
            holder.bind(("::1", 0))
        except OSError:
            pytest.skip("IPv6 loopback not usable in this environment")
        holder.listen(1)
        port = holder.getsockname()[1]

        result = probe_bind(port, Protocol.TCP, "::1")
        assert result.available is False


def test_socket_creation_failure_is_handled_gracefully(monkeypatch):
    def _raise(*args, **kwargs):
        raise OSError("no sockets for you")

    monkeypatch.setattr(socket, "socket", _raise)
    result = probe_bind(12345, Protocol.TCP, "127.0.0.1")
    assert result.available is False
    assert "could not create probe socket" in result.reason


def test_permission_error_is_reported_distinctly(monkeypatch):
    class _FakeSocket:
        def bind(self, addr):
            raise PermissionError("denied")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: _FakeSocket())
    result = probe_bind(80, Protocol.TCP, "0.0.0.0")
    assert result.available is False
    assert "permission denied" in result.reason.lower()


def test_does_not_set_so_reuseaddr_or_reuseport(monkeypatch):
    """Guard against reintroducing SO_REUSEADDR/SO_REUSEPORT, which could
    make an occupied port falsely appear available (see bindprobe.py's
    module docstring for the full reasoning).
    """
    calls = []
    original_socket = socket.socket

    class _TrackingSocket(original_socket):
        def setsockopt(self, *args, **kwargs):
            calls.append(args)
            return super().setsockopt(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", _TrackingSocket)
    probe_bind(_free_tcp_port(), Protocol.TCP, "127.0.0.1")
    assert calls == []
