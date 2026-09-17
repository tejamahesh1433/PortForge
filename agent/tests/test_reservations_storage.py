"""Tests for reservation storage: load/save, atomic write, and the "never
silently destroy malformed data" safety guarantees.
"""
import json

import pytest

from portforge_agent.models import Protocol
from portforge_agent.reservations.models import Reservation
from portforge_agent.reservations.storage import ReservationStorageError, ReservationStore


def _reservation(port=8003, project="deeptrace", protocol=Protocol.TCP):
    return Reservation.create(host_id="host-a", port=port, project=project, protocol=protocol)


def test_load_missing_file_returns_empty_list(tmp_path):
    store = ReservationStore(tmp_path / "reservations.json")
    assert store.load() == []


def test_load_empty_file_returns_empty_list(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text("", encoding="utf-8")
    store = ReservationStore(path)
    assert store.load() == []


def test_save_then_load_round_trips(tmp_path):
    store = ReservationStore(tmp_path / "reservations.json")
    r = _reservation()
    store.save([r])

    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].reservation_id == r.reservation_id
    assert loaded[0].port == 8003
    assert loaded[0].project == "deeptrace"
    assert loaded[0].protocol == Protocol.TCP


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "reservations.json"
    store = ReservationStore(path)
    store.save([_reservation()])
    assert path.exists()


def test_save_is_atomic_no_temp_files_left_behind(tmp_path):
    store = ReservationStore(tmp_path / "reservations.json")
    store.save([_reservation()])
    leftover_tmp = list(tmp_path.glob(".reservations-*.tmp"))
    assert leftover_tmp == []


def test_save_overwrites_previous_content(tmp_path):
    store = ReservationStore(tmp_path / "reservations.json")
    store.save([_reservation(port=1)])
    store.save([_reservation(port=2)])
    loaded = store.load()
    assert [r.port for r in loaded] == [2]


def test_malformed_json_raises_and_does_not_modify_file(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError):
        store.load()

    # file untouched
    assert path.read_text(encoding="utf-8") == "{not valid json"


def test_unsupported_schema_version_raises(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text(json.dumps({"schema_version": 999, "reservations": []}), encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError, match="schema"):
        store.load()


def test_missing_schema_version_raises(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text(json.dumps({"reservations": []}), encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError):
        store.load()


def test_non_object_top_level_raises(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError):
        store.load()


def test_malformed_reservations_list_shape_raises(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text(json.dumps({"schema_version": 1, "reservations": "not-a-list"}), encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError):
        store.load()


def test_single_malformed_entry_fails_whole_load_not_silently_dropped(tmp_path):
    path = tmp_path / "reservations.json"
    good = _reservation(port=1).to_dict()
    bad = {"reservation_id": "x"}  # missing required fields
    path.write_text(json.dumps({"schema_version": 1, "reservations": [good, bad]}), encoding="utf-8")
    store = ReservationStore(path)

    with pytest.raises(ReservationStorageError):
        store.load()

    # Critically: the file must still contain BOTH entries -- a load
    # failure must never trigger a save that would drop the bad one.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw["reservations"]) == 2


def test_permission_error_on_read_raises_storage_error(tmp_path, monkeypatch):
    path = tmp_path / "reservations.json"
    path.write_text(json.dumps({"schema_version": 1, "reservations": []}), encoding="utf-8")
    store = ReservationStore(path)

    def _raise(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(type(path), "read_text", _raise)

    with pytest.raises(ReservationStorageError):
        store.load()


def test_permission_error_on_write_raises_storage_error(tmp_path, monkeypatch):
    store = ReservationStore(tmp_path / "reservations.json")

    import portforge_agent.reservations.storage as storage_module

    def _raise(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(storage_module.os, "replace", _raise)

    with pytest.raises(ReservationStorageError):
        store.save([_reservation()])


def test_tcp_and_udp_on_same_port_are_distinct_entries(tmp_path):
    store = ReservationStore(tmp_path / "reservations.json")
    tcp = _reservation(port=53, protocol=Protocol.TCP, project="a")
    udp = _reservation(port=53, protocol=Protocol.UDP, project="b")
    store.save([tcp, udp])

    loaded = store.load()
    assert len(loaded) == 2
    by_protocol = {r.protocol: r for r in loaded}
    assert by_protocol[Protocol.TCP].project == "a"
    assert by_protocol[Protocol.UDP].project == "b"


def test_reservation_to_dict_and_from_dict_round_trip():
    r = _reservation()
    restored = Reservation.from_dict(r.to_dict())
    assert restored.reservation_id == r.reservation_id
    assert restored.port == r.port
    assert restored.protocol == r.protocol
    assert restored.project == r.project
