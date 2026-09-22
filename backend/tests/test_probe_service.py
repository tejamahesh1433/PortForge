"""v1.1-B: probe lifecycle unit tests -- queue/deliver/complete/classify,
freshness, expiry, ownership, idempotent completion, offline/stale host.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.host import Host
from app.services import probe_service


def _make_host(db, host_id=None, last_seen=None) -> uuid.UUID:
    host_id = host_id or uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(
        Host(
            id=host_id,
            hostname="probe-test-host",
            operating_system="linux",
            first_seen=now,
            last_seen=last_seen or now,
        )
    )
    db.commit()
    return host_id


def test_queue_then_classify_before_delivery_is_not_remote_capable(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9000, "tcp", "0.0.0.0")
    db_session.commit()
    assert probe is not None
    assert probe.status == "PENDING"

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9000, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE


def test_full_lifecycle_free_result(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9001, "tcp", "0.0.0.0")
    db_session.commit()

    delivered = probe_service.claim_pending_probes(db_session, host_id)
    assert len(delivered) == 1
    assert delivered[0].status == "DELIVERED"

    result = probe_service.submit_result(db_session, host_id, probe.id, True, None)
    assert result.status == "COMPLETED"
    assert result.result_available is True

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9001, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.VERIFIED_FREE


def test_full_lifecycle_occupied_result(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9002, "tcp", "0.0.0.0")
    db_session.commit()
    probe_service.claim_pending_probes(db_session, host_id)
    probe_service.submit_result(db_session, host_id, probe.id, False, "bind failed: address in use")

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9002, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.VERIFIED_OCCUPIED


def test_failed_probe_attempt_is_unavailable(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9003, "tcp", "0.0.0.0")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, None, "could not create socket")

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9003, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.UNAVAILABLE
    assert evidence.probe.status == "FAILED"


def test_expired_completed_probe_is_expired_not_fresh(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9004, "tcp", "0.0.0.0")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, True, None)

    probe.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9004, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.EXPIRED


def test_expired_undelivered_probe_is_not_remote_capable_not_expired(db_session):
    """A probe that never even got delivered/answered and is now past its
    TTL should NOT be reported as "expired" (implying evidence existed and
    went stale) -- it should read the same as "no evidence at all".
    """
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9005, "tcp", "0.0.0.0")
    db_session.commit()
    probe.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    evidence = probe_service.get_probe_evidence(db_session, host_id, 9005, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE


def test_no_probe_at_all_is_not_remote_capable(db_session):
    host_id = _make_host(db_session)
    evidence = probe_service.get_probe_evidence(db_session, host_id, 9006, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE
    assert evidence.probe is None


def test_host_ownership_violation_rejected(db_session):
    host_a = _make_host(db_session)
    host_b = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_a, 9007, "tcp", "0.0.0.0")
    db_session.commit()

    try:
        probe_service.submit_result(db_session, host_b, probe.id, True, None)
        assert False, "expected ProbeOwnershipError"
    except probe_service.ProbeOwnershipError:
        pass

    # unaffected -- still exactly as it was
    evidence = probe_service.get_probe_evidence(db_session, host_a, 9007, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE
    assert evidence.probe.status == "PENDING"


def test_unknown_probe_id_raises_lookup_error(db_session):
    host_id = _make_host(db_session)
    try:
        probe_service.submit_result(db_session, host_id, uuid.uuid4(), True, None)
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_duplicate_completion_is_idempotent_first_result_wins(db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 9008, "tcp", "0.0.0.0")
    db_session.commit()

    first = probe_service.submit_result(db_session, host_id, probe.id, True, "first")
    assert first.result_available is True

    second = probe_service.submit_result(db_session, host_id, probe.id, False, "second, should be ignored")
    assert second.result_available is True  # unchanged -- first submission wins
    assert second.result_reason == "first"


def test_claim_only_delivers_pending_not_already_delivered(db_session):
    host_id = _make_host(db_session)
    probe_service.queue_probe(db_session, host_id, 9009, "tcp", "0.0.0.0")
    db_session.commit()

    first_claim = probe_service.claim_pending_probes(db_session, host_id)
    assert len(first_claim) == 1

    second_claim = probe_service.claim_pending_probes(db_session, host_id)
    assert len(second_claim) == 0  # already DELIVERED, not re-delivered


def test_claim_bounded_by_max_probes_delivered_per_heartbeat(db_session):
    from app.config import get_settings

    host_id = _make_host(db_session)
    bound = get_settings().max_probes_delivered_per_heartbeat
    for i in range(bound + 5):
        probe_service.queue_probe(db_session, host_id, 10000 + i, "tcp", "0.0.0.0")
        db_session.commit()

    delivered = probe_service.claim_pending_probes(db_session, host_id)
    assert len(delivered) == bound


def test_queue_bounded_by_max_pending_probes_per_host(db_session):
    from app.config import get_settings

    host_id = _make_host(db_session)
    bound = get_settings().max_pending_probes_per_host
    queued = 0
    for i in range(bound + 5):
        probe = probe_service.queue_probe(db_session, host_id, 11000 + i, "tcp", "0.0.0.0")
        db_session.commit()
        if probe is not None:
            queued += 1
    assert queued == bound


def test_queue_for_unknown_host_is_a_safe_no_op(db_session):
    result = probe_service.queue_probe(db_session, uuid.uuid4(), 9010, "tcp", "0.0.0.0")
    assert result is None  # no FK violation, no exception


def test_queue_for_offline_host_is_a_safe_no_op(db_session):
    host_id = _make_host(db_session, last_seen=datetime.now(timezone.utc) - timedelta(seconds=400))
    result = probe_service.queue_probe(db_session, host_id, 9011, "tcp", "0.0.0.0")
    assert result is None


def test_queue_for_stale_host_is_a_safe_no_op(db_session):
    host_id = _make_host(db_session, last_seen=datetime.now(timezone.utc) - timedelta(seconds=200))
    result = probe_service.queue_probe(db_session, host_id, 9012, "tcp", "0.0.0.0")
    assert result is None


def test_queue_for_healthy_host_succeeds(db_session):
    host_id = _make_host(db_session, last_seen=datetime.now(timezone.utc))
    result = probe_service.queue_probe(db_session, host_id, 9013, "tcp", "0.0.0.0")
    assert result is not None
