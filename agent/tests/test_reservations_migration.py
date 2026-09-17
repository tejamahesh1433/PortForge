"""Tests for migrating legacy (Phase 4) hostname-based reservation host_ids
to the Phase 5 persistent UUID identity.
"""
from portforge_agent.models import Protocol
from portforge_agent.reservations.migration import migrate_if_needed, migrate_legacy_reservations
from portforge_agent.reservations.models import Reservation
from portforge_agent.reservations.storage import ReservationStore


def _legacy_reservation(host_id="old-hostname", port=8003, project="deeptrace"):
    return Reservation.create(host_id=host_id, port=port, project=project, protocol=Protocol.TCP)


def _isolated_store(tmp_path, monkeypatch):
    reservations_path = tmp_path / "reservations.json"
    lock_path = tmp_path / "reservations.lock"
    monkeypatch.setattr("portforge_agent.reservations.migration.default_reservations_path", lambda: reservations_path)
    monkeypatch.setattr("portforge_agent.reservations.migration.default_lock_path", lambda: lock_path)
    return ReservationStore(reservations_path)


def test_migrates_matching_legacy_reservations(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    legacy = _legacy_reservation(host_id="old-hostname")
    store.save([legacy])

    migrated_count = migrate_legacy_reservations("new-uuid-1234", "old-hostname")

    assert migrated_count == 1
    reloaded = store.load()
    assert len(reloaded) == 1
    assert reloaded[0].host_id == "new-uuid-1234"


def test_preserves_reservation_id_timestamps_and_ownership(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    legacy = _legacy_reservation(host_id="old-hostname", port=8003, project="deeptrace")
    store.save([legacy])

    migrate_legacy_reservations("new-uuid-1234", "old-hostname")

    reloaded = store.load()[0]
    assert reloaded.reservation_id == legacy.reservation_id
    assert reloaded.created_at == legacy.created_at
    assert reloaded.updated_at == legacy.updated_at
    assert reloaded.project == "deeptrace"
    assert reloaded.port == 8003


def test_does_not_touch_reservations_for_other_hosts(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    legacy = _legacy_reservation(host_id="old-hostname", port=1)
    unrelated = _legacy_reservation(host_id="some-other-host", port=2)
    store.save([legacy, unrelated])

    migrate_legacy_reservations("new-uuid-1234", "old-hostname")

    reloaded = {r.port: r.host_id for r in store.load()}
    assert reloaded[1] == "new-uuid-1234"
    assert reloaded[2] == "some-other-host"  # untouched


def test_migration_is_idempotent(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    store.save([_legacy_reservation(host_id="old-hostname")])

    first = migrate_legacy_reservations("new-uuid-1234", "old-hostname")
    second = migrate_legacy_reservations("new-uuid-1234", "old-hostname")

    assert first == 1
    assert second == 0  # nothing left to migrate
    assert len(store.load()) == 1  # no duplication


def test_no_op_when_no_legacy_reservations(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    store.save([_legacy_reservation(host_id="new-uuid-1234")])  # already migrated

    migrated_count = migrate_legacy_reservations("new-uuid-1234", "old-hostname")
    assert migrated_count == 0


def test_no_op_when_no_reservations_file_at_all(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)  # sets up paths but never saves
    migrated_count = migrate_legacy_reservations("new-uuid-1234", "old-hostname")
    assert migrated_count == 0


def test_no_op_when_new_and_legacy_ids_are_identical(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    store.save([_legacy_reservation(host_id="same-id")])
    migrated_count = migrate_legacy_reservations("same-id", "same-id")
    assert migrated_count == 0


def test_does_not_lose_reservations_on_mixed_migration(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    reservations = [
        _legacy_reservation(host_id="old-hostname", port=1, project="a"),
        _legacy_reservation(host_id="old-hostname", port=2, project="b"),
        _legacy_reservation(host_id="already-new-uuid", port=3, project="c"),
    ]
    store.save(reservations)

    migrated_count = migrate_legacy_reservations("new-uuid-1234", "old-hostname")

    assert migrated_count == 2
    reloaded = store.load()
    assert len(reloaded) == 3  # nothing lost
    assert {r.project for r in reloaded} == {"a", "b", "c"}


def test_migrate_if_needed_uses_current_hostname(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr("portforge_agent.reservations.migration.pf.get_hostname", lambda: "this-machine")
    store.save([_legacy_reservation(host_id="this-machine")])

    migrated_count = migrate_if_needed("new-persistent-uuid")

    assert migrated_count == 1
    assert store.load()[0].host_id == "new-persistent-uuid"
