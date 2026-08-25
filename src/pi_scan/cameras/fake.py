"""Controllable camera implementation for tests and the desktop simulator."""

from collections.abc import Sequence
from pathlib import Path
from time import sleep

from .base import CameraIdentity


class FakeCamera:
    def __init__(
        self,
        identifier: str,
        *,
        files: dict[str, bytes] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self._identity = CameraIdentity(identifier, "Simulated camera", "fake")
        self.files = files or {".jpg": b"fake-jpeg-data"}
        self.failure = failure
        self.capture_count = 0

    @property
    def identity(self) -> CameraIdentity:
        return self._identity

    def capture(self, destination_base: Path) -> Sequence[Path]:
        self.capture_count += 1
        if self.failure is not None:
            raise self.failure
        results: list[Path] = []
        for suffix, data in self.files.items():
            path = destination_base.with_name(destination_base.name + suffix)
            path.write_bytes(data)
            results.append(path)
        return results


class SimulatedCameraError(RuntimeError):
    """A controllable simulator camera rejected an operation."""


class SimulatedCamera(FakeCamera):
    """Fake camera with the lifecycle expected by a complete scanner session."""

    def __init__(
        self,
        identifier: str,
        *,
        files: dict[str, bytes] | None = None,
        operation_delay: float = 0.0,
    ) -> None:
        super().__init__(identifier, files=files)
        if operation_delay < 0:
            raise ValueError("operation_delay cannot be negative")
        self.operation_delay = operation_delay
        self.connected = True
        self.prepared = False
        self.focus_locked = False

    def probe(self) -> None:
        self._wait()
        if not self.connected:
            raise SimulatedCameraError(f"{self.identity.identifier} is disconnected")

    def prepare(self) -> None:
        self.probe()
        self.prepared = True
        self.focus_locked = False

    def autofocus_and_lock(self) -> None:
        self.probe()
        if not self.prepared:
            raise SimulatedCameraError(f"{self.identity.identifier} is not prepared")
        self.focus_locked = True

    def capture(self, destination_base: Path) -> Sequence[Path]:
        self.probe()
        if not self.prepared or not self.focus_locked:
            raise SimulatedCameraError(f"{self.identity.identifier} is not prepared and focused")
        return super().capture(destination_base)

    def _wait(self) -> None:
        if self.operation_delay:
            sleep(self.operation_delay)
