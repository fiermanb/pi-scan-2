"""Dynamic free-space checks before camera capture begins."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .removable import StorageError


class InsufficientStorage(StorageError):
    def __init__(self, free_bytes: int, required_bytes: int) -> None:
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        super().__init__(
            f"insufficient storage: {free_bytes} bytes free; "
            f"{required_bytes} bytes must remain available"
        )


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total: int
    used: int
    free: int


UsageReader = Callable[[Path], DiskUsage]


class StorageCapacityGuard:
    def __init__(
        self,
        path: Path,
        *,
        reserve_bytes: int = 256 * 1024 * 1024,
        usage_reader: UsageReader | None = None,
    ) -> None:
        if reserve_bytes < 0:
            raise ValueError("reserve_bytes cannot be negative")
        self.path = path
        self.reserve_bytes = reserve_bytes
        self._usage_reader = usage_reader or _disk_usage

    def check(self) -> None:
        try:
            usage = self._usage_reader(self.path)
        except OSError as error:
            raise StorageError(f"could not inspect free space for {self.path}: {error}") from error
        if usage.free < self.reserve_bytes:
            raise InsufficientStorage(usage.free, self.reserve_bytes)


def _disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(usage.total, usage.used, usage.free)
