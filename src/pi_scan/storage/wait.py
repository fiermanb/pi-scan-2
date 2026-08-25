"""Cancellable polling for removable media inserted after application start."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import Event
from time import monotonic, sleep

from .removable import RemovableStorage, StorageError, StorageVolume


class StorageWaitState(StrEnum):
    NO_VOLUME = "no_volume"
    MULTIPLE_VOLUMES = "multiple_volumes"
    DISCOVERY_FAILED = "discovery_failed"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class StorageWaitStatus:
    state: StorageWaitState
    message: str
    volume_count: int = 0
    error: str | None = None


class StorageWaitCancelled(StorageError):
    pass


class StorageWaitTimeout(StorageError):
    pass


StatusSink = Callable[[StorageWaitStatus], None]


def _ignore_status(status: StorageWaitStatus) -> None:
    del status


class StorageWaiter:
    def __init__(
        self,
        storage: RemovableStorage,
        *,
        interval: float = 1.0,
        status_sink: StatusSink | None = None,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isfinite(interval) or interval <= 0:
            raise ValueError("interval must be finite and positive")
        self.storage = storage
        self.interval = interval
        self.status_sink: StatusSink = status_sink or _ignore_status
        self._sleep = sleeper
        self._clock = clock
        self._cancelled = Event()
        self._last_status: StorageWaitStatus | None = None
        self._ready_volume: StorageVolume | None = None

    def cancel(self) -> None:
        self._cancelled.set()

    def poll(self) -> StorageWaitStatus:
        try:
            volumes = self.storage.discover()
        except StorageError as error:
            self._ready_volume = None
            return self._publish(
                StorageWaitStatus(
                    StorageWaitState.DISCOVERY_FAILED,
                    "Could not inspect removable storage",
                    error=str(error),
                )
            )
        if not volumes:
            self._ready_volume = None
            return self._publish(
                StorageWaitStatus(
                    StorageWaitState.NO_VOLUME,
                    "Insert one removable storage device",
                )
            )
        if len(volumes) > 1:
            self._ready_volume = None
            return self._publish(
                StorageWaitStatus(
                    StorageWaitState.MULTIPLE_VOLUMES,
                    "Remove extra storage devices; exactly one is required",
                    volume_count=len(volumes),
                )
            )
        volume = next(iter(volumes))
        self._ready_volume = volume
        return self._publish(
            StorageWaitStatus(
                StorageWaitState.READY,
                f"Found removable storage: {volume.device_path}",
                volume_count=1,
            )
        )

    def wait(self, *, timeout: float | None = None) -> StorageVolume:
        if timeout is not None and (not isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and non-negative")
        started = self._clock()
        while True:
            if self._cancelled.is_set():
                raise StorageWaitCancelled("storage wait was cancelled")
            status = self.poll()
            if status.state is StorageWaitState.READY:
                if self._ready_volume is None:
                    raise StorageError("storage became unavailable during selection")
                return self._ready_volume
            if timeout is not None and self._clock() - started >= timeout:
                raise StorageWaitTimeout(f"no single removable volume found within {timeout:g}s")
            self._sleep(self.interval)

    def _publish(self, status: StorageWaitStatus) -> StorageWaitStatus:
        if status != self._last_status:
            self.status_sink(status)
            self._last_status = status
        return status
