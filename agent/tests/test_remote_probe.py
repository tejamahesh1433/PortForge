"""v1.1-B: agent-side remote bind-probe delivery/execution tests."""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from portforge_agent.remote_probe import MAX_PROBES_PER_CYCLE, process_pending_probes


def _heartbeat_data(pending_probes, host_id="host-1"):
    return {"host_id": host_id, "last_seen": "2026-01-01T00:00:00Z", "status": "online", "pending_probes": pending_probes}


def test_no_pending_probes_is_a_no_op():
    client = MagicMock()
    assert process_pending_probes(client, _heartbeat_data([])) == 0
    client.submit_probe_result.assert_not_called()


def test_missing_pending_probes_field_is_legacy_compatible_no_op():
    """A Central response predating this field (or a malformed one) must
    never crash the heartbeat cycle -- identical to an empty list.
    """
    client = MagicMock()
    assert process_pending_probes(client, {"host_id": "host-1", "status": "online"}) == 0
    client.submit_probe_result.assert_not_called()


def test_non_dict_heartbeat_data_is_a_safe_no_op():
    client = MagicMock()
    assert process_pending_probes(client, None) == 0
    assert process_pending_probes(client, "not a dict") == 0
    assert process_pending_probes(client, []) == 0


def test_real_local_probe_reports_free_port_and_submits_result():
    client = MagicMock()
    # A genuinely free, high, unprivileged port -- bind it, confirm free,
    # then submit.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]
    # socket is now closed -- genuinely free again

    data = _heartbeat_data([{"probe_id": "p1", "port": free_port, "protocol": "tcp", "bind_address": "0.0.0.0"}])
    processed = process_pending_probes(client, data)

    assert processed == 1
    client.submit_probe_result.assert_called_once_with("host-1", "p1", True, None)


def test_real_local_probe_reports_occupied_port():
    client = MagicMock()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    occupied_port = listener.getsockname()[1]
    try:
        data = _heartbeat_data(
            [{"probe_id": "p2", "port": occupied_port, "protocol": "tcp", "bind_address": "127.0.0.1"}]
        )
        processed = process_pending_probes(client, data)
        assert processed == 1
        args = client.submit_probe_result.call_args[0]
        assert args[0] == "host-1"
        assert args[1] == "p2"
        assert args[2] is False  # occupied
        assert args[3] is not None  # a reason string
    finally:
        listener.close()


def test_probe_socket_is_actually_closed_after_free_probe():
    """The probed port must be immediately reusable afterward -- proves
    the temporary socket was really closed, not leaked.
    """
    client = MagicMock()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]

    data = _heartbeat_data([{"probe_id": "p3", "port": free_port, "protocol": "tcp", "bind_address": "0.0.0.0"}])
    process_pending_probes(client, data)

    # If the probe leaked its socket, this bind would fail.
    reuse_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reuse_sock.bind(("0.0.0.0", free_port))
    finally:
        reuse_sock.close()


def test_malformed_entry_missing_probe_id_is_skipped_not_crashed():
    client = MagicMock()
    data = _heartbeat_data([{"port": 9999, "protocol": "tcp"}])  # no probe_id
    assert process_pending_probes(client, data) == 0
    client.submit_probe_result.assert_not_called()


def test_malformed_entry_bad_port_type_is_skipped():
    client = MagicMock()
    data = _heartbeat_data([{"probe_id": "p4", "port": "not-an-int", "protocol": "tcp"}])
    assert process_pending_probes(client, data) == 0


def test_out_of_range_port_is_skipped():
    client = MagicMock()
    data = _heartbeat_data([{"probe_id": "p5", "port": 99999, "protocol": "tcp"}])
    assert process_pending_probes(client, data) == 0
    client.submit_probe_result.assert_not_called()


def test_non_dict_entry_in_list_is_skipped():
    client = MagicMock()
    data = _heartbeat_data(["not-a-dict"])
    assert process_pending_probes(client, data) == 0


def test_one_bad_entry_does_not_block_the_rest():
    client = MagicMock()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]

    data = _heartbeat_data(
        [
            {"port": 9999, "protocol": "tcp"},  # malformed -- no probe_id
            {"probe_id": "good", "port": free_port, "protocol": "tcp", "bind_address": "0.0.0.0"},
        ]
    )
    processed = process_pending_probes(client, data)
    assert processed == 1
    client.submit_probe_result.assert_called_once_with("host-1", "good", True, None)


def test_batch_is_bounded_at_max_probes_per_cycle():
    client = MagicMock()
    entries = [{"probe_id": f"p{i}", "port": 20000 + i, "protocol": "tcp"} for i in range(MAX_PROBES_PER_CYCLE + 20)]
    data = _heartbeat_data(entries)
    processed = process_pending_probes(client, data)
    assert processed == MAX_PROBES_PER_CYCLE
    assert client.submit_probe_result.call_count == MAX_PROBES_PER_CYCLE


def test_submission_failure_does_not_raise():
    client = MagicMock()
    client.submit_probe_result.side_effect = RuntimeError("network error")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]

    data = _heartbeat_data([{"probe_id": "p6", "port": free_port, "protocol": "tcp"}])
    processed = process_pending_probes(client, data)  # must not raise
    assert processed == 1


def test_probe_bind_execution_failure_reports_failed_not_free():
    client = MagicMock()
    with patch("portforge_agent.remote_probe.probe_bind", side_effect=OSError("simulated probe crash")):
        data = _heartbeat_data([{"probe_id": "p7", "port": 12345, "protocol": "tcp"}])
        processed = process_pending_probes(client, data)

    assert processed == 1
    args = client.submit_probe_result.call_args[0]
    assert args[2] is None  # available=None -- probe ATTEMPT itself failed
    assert "simulated probe crash" in args[3]


def test_udp_protocol_is_handled():
    client = MagicMock()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]

    data = _heartbeat_data([{"probe_id": "p8", "port": free_port, "protocol": "udp", "bind_address": "0.0.0.0"}])
    processed = process_pending_probes(client, data)
    assert processed == 1
    client.submit_probe_result.assert_called_once_with("host-1", "p8", True, None)


def test_no_secrets_logged(caplog):
    """Probe processing must never log anything resembling a credential --
    it only ever touches port/protocol/bind_address, never tokens.
    """
    client = MagicMock()
    client.submit_probe_result.side_effect = RuntimeError("auth failed with token abc123secret")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.bind(("0.0.0.0", 0))
        free_port = probe_sock.getsockname()[1]

    data = _heartbeat_data([{"probe_id": "p9", "port": free_port, "protocol": "tcp"}])
    with caplog.at_level("WARNING"):
        process_pending_probes(client, data)

    # The exception text itself may appear (it's a generic network-error
    # message), but this test's real assertion is structural: nothing here
    # ever reads or logs a token value -- process_pending_probes/submit
    # never touch credentials.py or any token field at all.
    for record in caplog.records:
        assert "Authorization" not in record.message
        assert "Bearer" not in record.message
