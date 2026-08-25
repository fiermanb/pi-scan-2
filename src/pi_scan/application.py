"""Application service joining discovery, configuration, and scanner sessions."""

from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from pi_scan.cameras.chdk import (
    ChdkCamera,
    ChdkCaptureSettings,
    ChdkDevice,
    ChdkPtpTransport,
    capture_settings_from_legacy,
    discover,
)
from pi_scan.domain.capture import CapturePairError, CapturePairResult
from pi_scan.domain.configuration import (
    CameraConfiguration,
    CameraSide,
    ScannerConfiguration,
    load_legacy_configuration,
    save_legacy_configuration,
)
from pi_scan.domain.session import (
    ScannerSession,
    SessionCamera,
    SessionOperationError,
    assign_camera_pair,
)
from pi_scan.events import ApplicationEvent, EventKind, JsonValue
from pi_scan.preview import DetailPair, PreviewError, PreviewGenerator

type EventSink = Callable[[ApplicationEvent], None]
type DeviceDiscovery = Callable[[ChdkPtpTransport], Sequence[ChdkDevice]]
type CameraFactory = Callable[[ChdkDevice, ChdkPtpTransport, ChdkCaptureSettings], SessionCamera]
type SessionCameraDiscovery = Callable[[], Sequence[SessionCamera]]


_AUDIBLE_FAILURES = frozenset({"prepare", "focus", "capture", "rescan", "recover"})


def _ignore_event(event: ApplicationEvent) -> None:
    del event


def _make_chdk_camera(
    device: ChdkDevice,
    transport: ChdkPtpTransport,
    settings: ChdkCaptureSettings,
) -> SessionCamera:
    return ChdkCamera(device, transport, settings=settings)


class PiScanApplication:
    """Synchronous use-case API designed to be called by a UI worker."""

    def __init__(
        self,
        transport: ChdkPtpTransport,
        image_directory: Path,
        configuration: ScannerConfiguration,
        *,
        event_sink: EventSink = _ignore_event,
        device_discovery: DeviceDiscovery = discover,
        camera_factory: CameraFactory = _make_chdk_camera,
        session_camera_discovery: SessionCameraDiscovery | None = None,
        configuration_path: Path | None = None,
        before_capture: Callable[[], None] | None = None,
        diagnostics_directory: Path | None = None,
        preview_generator: PreviewGenerator | None = None,
    ) -> None:
        self.transport = transport
        self.image_directory = image_directory
        self.configuration = configuration
        self.event_sink = event_sink
        self.device_discovery = device_discovery
        self.camera_factory = camera_factory
        self.session_camera_discovery = session_camera_discovery
        self.configuration_path = configuration_path
        self.before_capture = before_capture or (lambda: None)
        self.diagnostics_directory = diagnostics_directory
        self.preview_generator = preview_generator or PreviewGenerator(
            image_directory / ".pi-scan-previews"
        )
        self.session: ScannerSession | None = None

    @classmethod
    def from_legacy_paths(
        cls,
        transport: ChdkPtpTransport,
        image_directory: Path,
        configuration_path: Path,
        *,
        event_sink: EventSink = _ignore_event,
        device_discovery: DeviceDiscovery = discover,
        camera_factory: CameraFactory = _make_chdk_camera,
        session_camera_discovery: SessionCameraDiscovery | None = None,
        before_capture: Callable[[], None] | None = None,
        diagnostics_directory: Path | None = None,
        preview_generator: PreviewGenerator | None = None,
    ) -> "PiScanApplication":
        configuration = (
            load_legacy_configuration(configuration_path)
            if configuration_path.exists()
            else ScannerConfiguration({})
        )
        return cls(
            transport,
            image_directory,
            configuration,
            event_sink=event_sink,
            device_discovery=device_discovery,
            camera_factory=camera_factory,
            session_camera_discovery=session_camera_discovery,
            configuration_path=configuration_path,
            before_capture=before_capture,
            diagnostics_directory=diagnostics_directory,
            preview_generator=preview_generator,
        )

    def initialize(self) -> ScannerSession:
        self._emit(EventKind.DISCOVERY_STARTED, "Searching for cameras")
        try:
            if self.session_camera_discovery is not None:
                cameras = list(self.session_camera_discovery())
            else:
                devices = list(self.device_discovery(self.transport))
                cameras: list[SessionCamera] = []
                for device in devices:
                    camera_configuration = self.configuration.camera(device.identity.identifier)
                    settings = capture_settings_from_legacy(camera_configuration)
                    cameras.append(self.camera_factory(device, self.transport, settings))
            pair = assign_camera_pair(cameras, self.configuration)
            self.session = ScannerSession(pair, self.image_directory)
        except Exception as error:
            self._emit_failure("initialize", error)
            raise

        self._emit(
            EventKind.CAMERAS_ASSIGNED,
            "Two cameras assigned",
            {
                "even_camera": pair.even.identity.identifier,
                "odd_camera": pair.odd.identity.identifier,
                "even_backend": pair.even.identity.backend,
                "odd_backend": pair.odd.identity.backend,
                "even_zoom": self.configuration.camera(pair.even.identity.identifier).zoom,
                "odd_zoom": self.configuration.camera(pair.odd.identity.identifier).zoom,
                "even_shutter": self.configuration.camera(pair.even.identity.identifier).shutter,
                "odd_shutter": self.configuration.camera(pair.odd.identity.identifier).shutter,
                "next_even_page": self.session.next_even_page,
            },
        )
        self._emit_state()
        return self.session

    def prepare(self) -> None:
        self._operation("prepare", lambda session: session.prepare())

    def focus(self) -> None:
        self._operation("focus", lambda session: session.focus())

    def dismiss_failure(self) -> None:
        self._operation("dismiss_failure", lambda session: session.dismiss_failure())

    def recover(self) -> None:
        self._operation("recover", lambda session: session.recover())

    def finish(self) -> None:
        self._operation("finish", lambda session: session.finish())

    def ensure_can_finish(self) -> None:
        self._require_session().ensure_can_finish()

    def configure_camera(
        self,
        identifier: str,
        *,
        zoom: str | None = None,
        shutter: str | None = None,
    ) -> CameraConfiguration:
        try:
            session = self._require_session()
            if session.state.value != "new":
                raise RuntimeError("camera settings can only be changed before preparation")
            camera = next(
                (
                    candidate
                    for candidate in (session.cameras.even, session.cameras.odd)
                    if candidate.identity.identifier == identifier
                ),
                None,
            )
            if camera is None:
                raise ValueError(f"unknown camera identifier: {identifier}")
            if camera.identity.backend != "chdk" or not isinstance(camera, ChdkCamera):
                raise RuntimeError(
                    f"camera {identifier} uses {camera.identity.backend}; configure it manually"
                )
            updated = self.configuration.with_camera_settings(
                identifier,
                zoom=zoom,
                shutter=shutter,
            )
            settings = capture_settings_from_legacy(updated.camera(identifier))
            previous_settings = camera.settings
            camera.configure(settings)
            try:
                if self.configuration_path is not None:
                    save_legacy_configuration(self.configuration_path, updated)
            except Exception:
                camera.configure(previous_settings)
                raise
            self.configuration = updated
        except Exception as error:
            self._emit_failure("configure_camera", error)
            raise
        selected = updated.camera(identifier)
        self._emit(
            EventKind.CAMERA_SETTINGS_CHANGED,
            f"Settings updated for {identifier}",
            {
                "identifier": identifier,
                "zoom": selected.zoom,
                "shutter": selected.shutter,
            },
        )
        return selected

    def test_capture(self, identifier: str) -> Path:
        session = self._require_session()
        if session.state.value != "new":
            raise RuntimeError("test shots can only be taken before preparation")
        pair = session.cameras
        camera = next(
            (
                candidate
                for candidate in (pair.even, pair.odd)
                if candidate.identity.identifier == identifier
            ),
            None,
        )
        if camera is None:
            raise ValueError(f"unknown camera identifier: {identifier}")
        if camera.identity.backend != "chdk" or not isinstance(camera, ChdkCamera):
            raise RuntimeError(
                f"camera {identifier} uses {camera.identity.backend}; test it manually"
            )
        side = "even" if pair.even is camera else "odd"
        try:
            self.preview_generator.directory.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix=".pi-scan-test-",
                dir=self.preview_generator.directory,
            ) as directory:
                camera.probe()
                camera.prepare()
                files = tuple(camera.capture(Path(directory) / "test"))
                jpeg = next(
                    (path for path in files if path.suffix.lower() in {".jpg", ".jpeg"}),
                    None,
                )
                if jpeg is None:
                    raise RuntimeError("test capture produced no JPEG")
                preview = self.preview_generator.generate_test(jpeg, side=side)
        except Exception as error:
            self._emit_failure("test_capture", error)
            raise
        self._emit(
            EventKind.TEST_CAPTURE_SUCCEEDED,
            f"Test shot captured for {side} camera",
            {
                "identifier": identifier,
                "side": side,
                "preview": str(preview.path),
                "width": preview.width,
                "height": preview.height,
            },
        )
        return preview.path

    def inspect_detail(self, centre_x: float, centre_y: float) -> DetailPair:
        """Produce an unscaled window of the last pair so focus can be judged."""
        session = self._require_session()
        capture = session.last_capture
        if capture is None:
            raise RuntimeError("there is no captured pair to inspect")
        try:
            detail = self.preview_generator.generate_detail(capture, centre_x, centre_y)
        except PreviewError as error:
            self._emit_failure("inspect_detail", error)
            raise
        self._emit(
            EventKind.DETAIL_READY,
            "Full-resolution detail ready",
            {
                "even_detail": str(detail.even.path),
                "odd_detail": str(detail.odd.path),
                "even_detail_size": [detail.even.width, detail.even.height],
                "odd_detail_size": [detail.odd.width, detail.odd.height],
                "centre": [centre_x, centre_y],
            },
        )
        return detail

    def save_debug_logs(self) -> tuple[Path, ...]:
        session = self._require_session()
        if self.diagnostics_directory is None:
            raise RuntimeError("no diagnostics directory is configured")
        saved: list[Path] = []
        failures: dict[CameraSide, Exception] = {}
        for side, camera in (
            (CameraSide.EVEN, session.cameras.even),
            (CameraSide.ODD, session.cameras.odd),
        ):
            if not isinstance(camera, ChdkCamera):
                continue
            try:
                path = camera.download_romlog(self.diagnostics_directory / f"{side.value}-rom.log")
                saved.append(path)
                self._emit(
                    EventKind.DEBUG_LOG_SAVED,
                    f"Camera debug log saved to {path.name}",
                    {"path": str(path)},
                )
            except Exception as error:
                failures[side] = error
        if failures:
            error = SessionOperationError("debug_log", failures)
            self._emit_failure("debug_log", error)
            raise error
        return tuple(saved)

    def turn_off_cameras(self) -> tuple[str, ...]:
        """Press the power button on both CHDK cameras, as Pi Scan 1.5 offered."""
        session = self._require_session()
        if session.state.value not in {"new", "complete"}:
            raise RuntimeError("cameras can only be turned off before or after scanning")
        turned_off: list[str] = []
        failures: dict[CameraSide, Exception] = {}
        for side, camera in (
            (CameraSide.EVEN, session.cameras.even),
            (CameraSide.ODD, session.cameras.odd),
        ):
            if not isinstance(camera, ChdkCamera):
                continue
            try:
                camera.power_off()
            except Exception as error:
                failures[side] = error
            else:
                turned_off.append(camera.identity.identifier)
        if failures:
            error = SessionOperationError("turn_off_cameras", failures)
            self._emit_failure("turn_off_cameras", error)
            raise error
        self._emit(
            EventKind.OPERATION_STARTED,
            "Cameras turned off",
            {"operation": "turn_off_cameras", "cameras": list(turned_off)},
        )
        return tuple(turned_off)

    def _beep_failure(self) -> None:
        """Sound the camera failure tone; a silent camera must never mask an error."""
        if self.session is None:
            return
        for camera in (self.session.cameras.even, self.session.cameras.odd):
            if isinstance(camera, ChdkCamera):
                # A silent camera must never mask the failure that caused the beep.
                with suppress(Exception):
                    camera.beep_failure()

    def swap_cameras(self, *, save_to: Path | None = None) -> None:
        session = self._require_session()
        pair = session.swap_cameras()
        updated = self.configuration.with_camera_positions(
            even_identifier=pair.even.identity.identifier,
            odd_identifier=pair.odd.identity.identifier,
        )
        destination = save_to or self.configuration_path
        try:
            if destination is not None:
                save_legacy_configuration(destination, updated)
        except Exception:
            session.swap_cameras()
            raise
        self.configuration = updated
        self._emit(
            EventKind.CAMERAS_ASSIGNED,
            "Camera page roles swapped",
            {
                "even_camera": pair.even.identity.identifier,
                "odd_camera": pair.odd.identity.identifier,
                "even_backend": pair.even.identity.backend,
                "odd_backend": pair.odd.identity.backend,
                "even_zoom": self.configuration.camera(pair.even.identity.identifier).zoom,
                "odd_zoom": self.configuration.camera(pair.odd.identity.identifier).zoom,
                "even_shutter": self.configuration.camera(pair.even.identity.identifier).shutter,
                "odd_shutter": self.configuration.camera(pair.odd.identity.identifier).shutter,
            },
        )
        self._emit_state()

    def capture(self) -> CapturePairResult:
        result = self._operation("capture", lambda session: self._capture(session))
        result = cast(CapturePairResult, result)
        self._emit_capture(result, rescan=False)
        return result

    def rescan(self) -> CapturePairResult:
        result = self._operation("rescan", lambda session: self._rescan(session))
        result = cast(CapturePairResult, result)
        self._emit_capture(result, rescan=True)
        return result

    def _capture(self, session: ScannerSession) -> CapturePairResult:
        self.before_capture()
        return session.capture()

    def _rescan(self, session: ScannerSession) -> CapturePairResult:
        self.before_capture()
        return session.rescan()

    def _operation(self, name: str, action: Callable[[ScannerSession], object]) -> object:
        session = self._require_session()
        self._emit(
            EventKind.OPERATION_STARTED,
            f"{name.capitalize()} started",
            {"operation": name, "state": session.state.value},
        )
        try:
            result = action(session)
        except Exception as error:
            if name in _AUDIBLE_FAILURES:
                self._beep_failure()
            self._emit_failure(name, error)
            self._emit_state()
            raise
        self._emit_state()
        return result

    def _require_session(self) -> ScannerSession:
        if self.session is None:
            raise RuntimeError("application must be initialized before camera operations")
        return self.session

    def _emit_capture(self, result: CapturePairResult, *, rescan: bool) -> None:
        details: dict[str, JsonValue] = {
            "even_page": result.even_page,
            "odd_page": result.odd_page,
            "rescan": rescan,
        }
        try:
            previews = self.preview_generator.generate(result)
        except PreviewError as error:
            self._emit_failure("preview", error)
        else:
            details.update(
                {
                    "even_preview": str(previews.even.path),
                    "odd_preview": str(previews.odd.path),
                    "even_preview_size": [previews.even.width, previews.even.height],
                    "odd_preview_size": [previews.odd.width, previews.odd.height],
                }
            )
        self._emit(
            EventKind.CAPTURE_SUCCEEDED,
            "Page pair rescanned" if rescan else "Page pair captured",
            details,
        )

    def _emit_state(self) -> None:
        session = self._require_session()
        self._emit(
            EventKind.STATE_CHANGED,
            f"Scanner state changed to {session.state.value}",
            {"state": session.state.value, "next_even_page": session.next_even_page},
        )

    def _emit_failure(self, operation: str, error: Exception) -> None:
        details: dict[str, JsonValue] = {
            "operation": operation,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        failures = None
        if isinstance(error, (SessionOperationError, CapturePairError)):
            failures = error.failures
        if failures:
            details["camera_failures"] = {
                str(side): {"type": type(failure).__name__, "error": str(failure)}
                for side, failure in failures.items()
            }
        if isinstance(error, CapturePairError) and error.recovery_directory is not None:
            details["recovery_directory"] = str(error.recovery_directory)
        self._emit(EventKind.OPERATION_FAILED, f"{operation.capitalize()} failed", details)

    def _emit(
        self,
        kind: EventKind,
        message: str,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        self.event_sink(ApplicationEvent(kind, message, details or {}))
