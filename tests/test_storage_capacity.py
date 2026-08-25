from pathlib import Path

import pytest

from pi_scan.storage import (
    DiskUsage,
    InsufficientStorage,
    StorageCapacityGuard,
    StorageError,
)


def test_capacity_guard_accepts_exact_reserve():
    guard = StorageCapacityGuard(
        Path("/media/scans"),
        reserve_bytes=100,
        usage_reader=lambda path: DiskUsage(1000, 900, 100),
    )
    guard.check()


def test_capacity_guard_reports_available_and_required_space():
    guard = StorageCapacityGuard(
        Path("/media/scans"),
        reserve_bytes=101,
        usage_reader=lambda path: DiskUsage(1000, 900, 100),
    )
    with pytest.raises(InsufficientStorage) as caught:
        guard.check()
    assert (caught.value.free_bytes, caught.value.required_bytes) == (100, 101)


def test_capacity_guard_maps_filesystem_failure():
    def fail(path):
        raise OSError("device disappeared")

    with pytest.raises(StorageError, match="device disappeared"):
        StorageCapacityGuard(Path("/media/scans"), usage_reader=fail).check()


def test_capacity_guard_rejects_negative_reserve():
    with pytest.raises(ValueError):
        StorageCapacityGuard(Path("."), reserve_bytes=-1)
