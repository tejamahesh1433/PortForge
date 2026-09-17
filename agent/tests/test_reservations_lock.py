"""Tests for the cross-platform reservation file lock."""
import threading
import time

import pytest

from portforge_agent.reservations.lock import LockTimeoutError, reservation_lock


def test_lock_can_be_acquired_and_released(tmp_path):
    lock_file = tmp_path / "reservations.lock"
    with reservation_lock(lock_file):
        pass  # should not raise
    # Can be acquired again immediately after release.
    with reservation_lock(lock_file):
        pass


def test_lock_creates_parent_directory(tmp_path):
    lock_file = tmp_path / "nested" / "reservations.lock"
    with reservation_lock(lock_file):
        pass
    assert lock_file.exists()


def test_lock_is_released_even_if_body_raises(tmp_path):
    lock_file = tmp_path / "reservations.lock"

    with pytest.raises(ValueError):
        with reservation_lock(lock_file):
            raise ValueError("boom")

    # Lock must be free again -- a short timeout should succeed instantly.
    with reservation_lock(lock_file, timeout=1.0):
        pass


def test_second_acquirer_blocks_until_first_releases(tmp_path):
    lock_file = tmp_path / "reservations.lock"
    order = []

    def holder():
        with reservation_lock(lock_file):
            order.append("holder-acquired")
            time.sleep(0.3)
            order.append("holder-released")

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.05)  # let the holder acquire first

    with reservation_lock(lock_file, timeout=2.0):
        order.append("second-acquired")

    t.join()

    # The second acquirer must not have gotten in before the holder released.
    assert order.index("second-acquired") > order.index("holder-released")


def test_lock_timeout_raises_when_held_too_long(tmp_path):
    lock_file = tmp_path / "reservations.lock"
    release_event = threading.Event()

    def holder():
        with reservation_lock(lock_file):
            release_event.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.1)

    try:
        with pytest.raises(LockTimeoutError):
            with reservation_lock(lock_file, timeout=0.3):
                pass
    finally:
        release_event.set()
        t.join()
