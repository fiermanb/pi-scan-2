"""Scanner-level camera assignment and workflow state transitions."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pi_scan.cameras.base import Camera

from .capture import CaptureCoordinator, CapturePairError, CapturePairResult
from .configuration import CameraSide, ScannerConfiguration
from .numbering import next_even_page


class SessionCamera(Camera, Protocol):
    """Camera operations required by the scanner session."""

    def probe(self) -> None: ...

    def prepare(self) -> object: ...

    def autofocus_and_lock(self) -> None: ...


class SessionState(StrEnum):
    NEW = "new"
    PREPARING = "preparing"
    READY_TO_FOCUS = "ready_to_focus"
    FOCUSING = "focusing"
    READY = "ready"
    CAPTURING = "capturing"
    CAPTURE_FAILED = "capture_failed"
    FAILED = "failed"
    COMPLETE = "complete"


class CameraAssignmentError(ValueError):
    """Exactly two distinct cameras could not be assigned safely."""


class InvalidSessionTransition(RuntimeError):
    """An operation was requested from an incompatible session state."""


class SessionOperationError(RuntimeError):
    """A scanner operation failed on one or both camera sides."""

    def __init__(self, operation: str, failures: dict[CameraSide, Exception]) -> None:
        super().__init__(f"{operation} failed on: {', '.join(side.value for side in failures)}")
        self.operation = operation
        self.failures = failures


@dataclass(frozen=True, slots=True)
class CameraPair:
    even: SessionCamera
    odd: SessionCamera

    def for_side(self, side: CameraSide) -> SessionCamera:
        return self.even if side is CameraSide.EVEN else self.odd

    def swapped(self) -> "CameraPair":
        return CameraPair(even=self.odd, odd=self.even)


def assign_camera_pair(
    cameras: list[SessionCamera], configuration: ScannerConfiguration
) -> CameraPair:
    """Assign two cameras, preferring persisted sides from the legacy configuration."""
    if len(cameras) != 2:
        raise CameraAssignmentError(f"expected exactly two cameras, found {len(cameras)}")
    identifiers = [camera.identity.identifier for camera in cameras]
    if len(set(identifiers)) != 2:
        raise CameraAssignmentError("camera identifiers must be distinct")

    assigned: dict[CameraSide, SessionCamera] = {}
    unassigned: list[SessionCamera] = []
    for camera in cameras:
        side = configuration.camera(camera.identity.identifier).position
        if side is None:
            unassigned.append(camera)
        elif side in assigned:
            raise CameraAssignmentError(f"multiple cameras are configured as {side.value}")
        else:
            assigned[side] = camera

    missing = [side for side in (CameraSide.ODD, CameraSide.EVEN) if side not in assigned]
    for side, camera in zip(missing, unassigned, strict=True):
        assigned[side] = camera
    return CameraPair(even=assigned[CameraSide.EVEN], odd=assigned[CameraSide.ODD])


class ScannerSession:
    """Coordinate the two cameras without depending on a UI framework."""

    def __init__(
        self,
        cameras: CameraPair,
        image_directory: Path,
        *,
        first_even_page: int | None = None,
    ) -> None:
        self.cameras = cameras
        self.image_directory = image_directory
        self.next_even_page = (
            next_even_page(image_directory) if first_even_page is None else first_even_page
        )
        if self.next_even_page < 0 or self.next_even_page % 2:
            raise ValueError("first_even_page must be a non-negative even number")
        self.state = SessionState.NEW
        self.last_capture: CapturePairResult | None = None
        self.last_error: Exception | None = None
        self._coordinator = CaptureCoordinator(cameras.even, cameras.odd)

    def prepare(self) -> None:
        self._require_state(SessionState.NEW, SessionState.FAILED)
        self.state = SessionState.PREPARING
        try:
            self._run_both("prepare", lambda camera: (camera.probe(), camera.prepare()))
        except SessionOperationError as error:
            self.last_error = error
            self.state = SessionState.FAILED
            raise
        self.last_error = None
        self.state = SessionState.READY_TO_FOCUS

    def focus(self) -> None:
        self._require_state(SessionState.READY_TO_FOCUS)
        self.state = SessionState.FOCUSING
        try:
            self._run_both("focus", lambda camera: camera.autofocus_and_lock())
        except SessionOperationError as error:
            self.last_error = error
            self.state = SessionState.FAILED
            raise
        self.last_error = None
        self.state = SessionState.READY

    def capture(self) -> CapturePairResult:
        self._require_state(SessionState.READY, SessionState.CAPTURE_FAILED)
        self.state = SessionState.CAPTURING
        try:
            result = self._coordinator.capture_pair(self.image_directory, self.next_even_page)
        except CapturePairError as error:
            self.last_error = error
            self.state = SessionState.CAPTURE_FAILED
            raise
        self.last_capture = result
        self.next_even_page += 2
        self.last_error = None
        self.state = SessionState.READY
        return result

    def dismiss_failure(self) -> None:
        """Acknowledge a failed capture without touching the cameras.

        Pi Scan 1.5 put a failed capture on its own screen whose only action
        returned to scanning, leaving the prepared and focused cameras alone.
        Recovery, which re-prepares and refocuses, stayed a separate choice.
        """
        self._require_state(SessionState.CAPTURE_FAILED)
        self.last_error = None
        self.state = SessionState.READY

    def recover(self) -> None:
        """Re-probe, prepare, and refocus both cameras after a workflow failure."""
        self._require_state(SessionState.FAILED, SessionState.CAPTURE_FAILED)
        self.state = SessionState.NEW
        self.prepare()
        self.focus()

    def finish(self) -> None:
        """Close the workflow so no further camera operations can start."""
        self.ensure_can_finish()
        self.state = SessionState.COMPLETE

    def ensure_can_finish(self) -> None:
        """Validate that the workflow may finish without changing its state."""
        self._require_state(
            SessionState.NEW,
            SessionState.READY_TO_FOCUS,
            SessionState.READY,
            SessionState.CAPTURE_FAILED,
            SessionState.FAILED,
        )

    def swap_cameras(self) -> CameraPair:
        """Swap page roles before focus and rebuild the capture coordinator."""
        self._require_state(SessionState.NEW, SessionState.READY_TO_FOCUS)
        self.cameras = self.cameras.swapped()
        self._coordinator = CaptureCoordinator(self.cameras.even, self.cameras.odd)
        return self.cameras

    def rescan(self) -> CapturePairResult:
        self._require_state(SessionState.READY, SessionState.CAPTURE_FAILED)
        if self.last_capture is None:
            raise InvalidSessionTransition("cannot rescan before a successful capture")
        self.state = SessionState.CAPTURING
        try:
            result = self._coordinator.capture_pair(
                self.image_directory, self.last_capture.even_page
            )
        except CapturePairError as error:
            self.last_error = error
            self.state = SessionState.CAPTURE_FAILED
            raise
        self.last_capture = result
        self.last_error = None
        self.state = SessionState.READY
        return result

    def _require_state(self, *allowed: SessionState) -> None:
        if self.state not in allowed:
            expected = ", ".join(state.value for state in allowed)
            raise InvalidSessionTransition(
                f"operation requires state {expected}; current state is {self.state.value}"
            )

    def _run_both(self, operation: str, action: Callable[[SessionCamera], object]) -> None:
        failures: dict[CameraSide, Exception] = {}
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix=f"pi-scan-{operation}",
        ) as executor:
            futures = {
                executor.submit(action, self.cameras.for_side(side)): side
                for side in (CameraSide.EVEN, CameraSide.ODD)
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    failures[futures[future]] = error
        if failures:
            raise SessionOperationError(operation, failures)
