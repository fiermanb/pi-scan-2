from pathlib import Path

import pytest

from pi_scan.storage import (
    StorageError,
    StorageVolume,
    StorageWaitCancelled,
    StorageWaiter,
    StorageWaitState,
    StorageWaitTimeout,
)


class SequencedStorage:
    def __init__(self, discoveries):
        self.discoveries = list(discoveries)

    def discover(self):
        current = self.discoveries[0]
        if len(self.discoveries) > 1:
            self.discoveries.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def volume(name="/dev/sdb1"):
    return StorageVolume(Path(name), "exfat")


def test_poll_distinguishes_missing_ambiguous_failed_and_ready():
    statuses = []
    storage = SequencedStorage(
        [(), (volume(), volume("/dev/sdc1")), StorageError("lsblk failed"), (volume(),)]
    )
    waiter = StorageWaiter(storage, status_sink=statuses.append)
    assert waiter.poll().state is StorageWaitState.NO_VOLUME
    assert waiter.poll().state is StorageWaitState.MULTIPLE_VOLUMES
    assert waiter.poll().state is StorageWaitState.DISCOVERY_FAILED
    assert waiter.poll().state is StorageWaitState.READY
    assert len(statuses) == 4


def test_wait_handles_hotplug_and_suppresses_duplicate_status_updates():
    statuses = []
    storage = SequencedStorage([(), (), (volume(),)])
    waiter = StorageWaiter(storage, sleeper=lambda seconds: None, status_sink=statuses.append)
    assert waiter.wait() == volume()
    assert [status.state for status in statuses] == [
        StorageWaitState.NO_VOLUME,
        StorageWaitState.READY,
    ]
    assert storage.discoveries == [(volume(),)]


def test_wait_can_be_cancelled():
    waiter = StorageWaiter(SequencedStorage([()]), sleeper=lambda seconds: waiter.cancel())
    with pytest.raises(StorageWaitCancelled):
        waiter.wait()


def test_wait_timeout_is_deterministic():
    times = iter([0.0, 0.0, 1.0])
    waiter = StorageWaiter(
        SequencedStorage([()]),
        sleeper=lambda seconds: None,
        clock=lambda: next(times),
    )
    with pytest.raises(StorageWaitTimeout):
        waiter.wait(timeout=1)


@pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_wait_rejects_invalid_timeout_without_polling_storage(timeout):
    storage = SequencedStorage([(volume(),)])
    waiter = StorageWaiter(storage)
    with pytest.raises(ValueError, match="finite and non-negative"):
        waiter.wait(timeout=timeout)
    assert storage.discoveries == [(volume(),)]


@pytest.mark.parametrize("interval", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_waiter_rejects_invalid_poll_interval(interval):
    with pytest.raises(ValueError, match="finite and positive"):
        StorageWaiter(SequencedStorage([()]), interval=interval)
