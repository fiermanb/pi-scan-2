"""Composition root for a physical Pi Scan appliance."""

import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic, sleep

from pi_scan import __version__
from pi_scan.application import EventSink, PiScanApplication
from pi_scan.cameras.chdk import (
    ChdkCamera,
    ChdkPtpTransport,
    capture_settings_from_legacy,
)
from pi_scan.cameras.chdk import (
    discover as discover_chdk,
)
from pi_scan.cameras.gphoto import (
    GphotoCamera,
    GphotoError,
    GphotoTransport,
)
from pi_scan.cameras.gphoto import (
    discover_cameras as discover_gphoto,
)
from pi_scan.diagnostics import EventFanout, JsonLineEventLog, TextEventLog
from pi_scan.domain.configuration import (
    ScannerConfiguration,
    load_legacy_configuration,
)
from pi_scan.domain.session import SessionCamera
from pi_scan.events import ApplicationEvent, EventKind, JsonValue
from pi_scan.preview import PreviewGenerator
from pi_scan.storage import (
    RemovableStorage,
    StorageCapacityGuard,
    StorageError,
    StorageVolume,
    StorageWaiter,
    StorageWaitStatus,
)
from pi_scan.update import UpdatePackage, apply_update, find_update


class ApplianceStartupError(RuntimeError):
    pass


def _ignore_event(event: ApplicationEvent) -> None:
    del event


def _verify_writable_storage(root: Path) -> None:
    """Prove the mounted filesystem can durably accept data before cameras fire."""
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=".pi-scan-write-test-",
            dir=root,
            delete=True,
        ) as probe:
            probe.write(b"pi-scan\n")
            probe.flush()
            os.fsync(probe.fileno())
    except OSError as error:
        raise StorageError(f"removable storage is not writable: {root}: {error}") from error


class PhysicalCameraDiscovery:
    """Create session cameras from CHDK first, then optional gPhoto2 devices."""

    def __init__(
        self,
        configuration: ScannerConfiguration,
        *,
        chdk_transport: ChdkPtpTransport,
        gphoto_transport: GphotoTransport | None = None,
        event_sink: EventSink = _ignore_event,
    ) -> None:
        self.configuration = configuration
        self.chdk_transport = chdk_transport
        self.gphoto_transport = gphoto_transport
        self.event_sink = event_sink

    def discover(self) -> tuple[SessionCamera, ...]:
        cameras: list[SessionCamera] = []
        for device in discover_chdk(
            self.chdk_transport,
            warning_sink=self._chdk_device_warning,
        ):
            settings = capture_settings_from_legacy(
                self.configuration.camera(device.identity.identifier)
            )
            cameras.append(ChdkCamera(device, self.chdk_transport, settings=settings))
        # Pi Scan 1.5 fell back to gPhoto2 only when no CHDK camera answered, so a
        # session is wholly CHDK or wholly gPhoto2 and never a mixture of the two.
        if not cameras and self.gphoto_transport is not None:
            try:
                devices = discover_gphoto(
                    self.gphoto_transport,
                    error_sink=self._gphoto_device_warning,
                )
            except GphotoError as error:
                self.event_sink(
                    ApplicationEvent(
                        EventKind.HARDWARE_WARNING,
                        f"gPhoto2 discovery unavailable; continuing with CHDK cameras: {error}",
                        {
                            "component": "gphoto2",
                            "error_type": type(error).__name__,
                            "error": str(error),
                        },
                    )
                )
                devices = ()
            known: set[str] = set()
            gphoto_identifiers: set[str] = set()
            for device in devices:
                # A CHDK Canon can also appear in gPhoto's generic PTP listing.
                # Prefer CHDK when both backends report the same stable serial.
                identifier = device.identity.identifier
                if identifier in gphoto_identifiers:
                    self.event_sink(
                        ApplicationEvent(
                            EventKind.HARDWARE_WARNING,
                            f"Ignoring duplicate gPhoto2 camera identifier {identifier!r} "
                            f"at {device.port}",
                            {
                                "component": "gphoto2",
                                "identifier": identifier,
                                "port": device.port,
                                "error": "duplicate stable camera identifier",
                            },
                        )
                    )
                    continue
                gphoto_identifiers.add(identifier)
                if identifier not in known:
                    cameras.append(GphotoCamera(device, self.gphoto_transport))
                    known.add(identifier)
        return tuple(cameras)

    def _chdk_device_warning(self, message: str) -> None:
        self.event_sink(
            ApplicationEvent(
                EventKind.HARDWARE_WARNING,
                f"CHDK ignored one unusable USB entry: {message}",
                {"component": "chdk", "error": message},
            )
        )

    def _gphoto_device_warning(self, model: str, port: str, error: GphotoError) -> None:
        self.event_sink(
            ApplicationEvent(
                EventKind.HARDWARE_WARNING,
                f"Ignoring unavailable gPhoto2 camera {model} at {port}: {error}",
                {
                    "component": "gphoto2",
                    "model": model,
                    "port": port,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
        )


@dataclass(slots=True)
class ApplianceRuntime:
    application: PiScanApplication
    storage: RemovableStorage
    volume: StorageVolume
    configuration_path: Path
    diagnostics_path: Path
    update_package: UpdatePackage | None = None
    force_eject_after: float = 60.0
    retry_interval: float = 1.0
    clock: Callable[[], float] = monotonic
    sleeper: Callable[[float], None] = sleep
    _ejected: bool = field(default=False, init=False)

    def eject(self) -> None:
        """Eject the scan media, forcing the unmount once 1.5's grace period expires."""
        if self._ejected:
            return
        deadline = self.clock() + self.force_eject_after
        last_error: StorageError | None = None
        while self.clock() < deadline:
            try:
                self.storage.eject(self.volume)
            except StorageError as error:
                last_error = error
                self.sleeper(self.retry_interval)
            else:
                self._ejected = True
                return
        forced_error: StorageError | None = None
        try:
            self.storage.unmount(self.volume, force=True)
        except StorageError as error:
            # An earlier attempt may already have unmounted the filesystem and
            # failed at the power-off, so the power-off is still owed a try.
            forced_error = error
        try:
            self.storage.eject(self.volume)
        except StorageError as error:
            raise StorageError(
                f"could not eject removable storage after {self.force_eject_after:g}s, "
                f"including a forced unmount: {error}"
            ) from (forced_error or last_error or error)
        self._ejected = True


class HardwareApplication:
    """Deferred appliance facade so storage waiting runs in the UI worker."""

    def __init__(
        self,
        storage: RemovableStorage,
        *,
        chdk_transport: ChdkPtpTransport | None = None,
        gphoto_transport: GphotoTransport | None = None,
        event_sink: EventSink = _ignore_event,
        storage_timeout: float | None = None,
        minimum_free_bytes: int = 256 * 1024 * 1024,
        waiter: StorageWaiter | None = None,
        force_eject_after: float = 60.0,
        retry_interval: float = 1.0,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.storage = storage
        self.chdk_transport = chdk_transport
        self.gphoto_transport = gphoto_transport
        self.event_sink = event_sink
        self.storage_timeout = storage_timeout
        self.minimum_free_bytes = minimum_free_bytes
        self.force_eject_after = force_eject_after
        self.retry_interval = retry_interval
        self.clock = clock
        self.sleeper = sleeper
        self.waiter = waiter or StorageWaiter(storage, status_sink=self._storage_status)
        self.runtime: ApplianceRuntime | None = None
        self._closed = False

    def initialize(self):
        if self._closed:
            raise ApplianceStartupError("hardware application is closed")
        if self.runtime is not None:
            raise ApplianceStartupError("hardware application is already initialized")
        selected = self.waiter.wait(timeout=self.storage_timeout)
        self.runtime = create_appliance(
            self.storage,
            selected_volume=selected,
            chdk_transport=self.chdk_transport,
            gphoto_transport=self.gphoto_transport,
            event_sink=self.event_sink,
            minimum_free_bytes=self.minimum_free_bytes,
            force_eject_after=self.force_eject_after,
            retry_interval=self.retry_interval,
            clock=self.clock,
            sleeper=self.sleeper,
        )
        return self.runtime.application.initialize()

    def prepare(self):
        return self._application().prepare()

    def focus(self):
        return self._application().focus()

    def dismiss_failure(self):
        return self._application().dismiss_failure()

    def recover(self):
        return self._application().recover()

    def swap_cameras(self):
        return self._application().swap_cameras()

    def turn_off_cameras(self):
        return self._application().turn_off_cameras()

    def apply_update(self) -> str:
        """Install an update carried on the scan media, then eject it."""
        self._application()
        if self.runtime is None:  # Kept explicit for strict type narrowing.
            raise ApplianceStartupError("hardware application is not initialized")
        package = self.runtime.update_package
        if package is None:
            raise ApplianceStartupError("no application update is present on the scan media")
        installed = apply_update(package)
        self.runtime.eject()
        self.event_sink(
            ApplicationEvent(
                EventKind.UPDATE_APPLIED,
                f"Installed Pi Scan {package.version}; restart to run it",
                {"version": package.version, "wheel": installed},
            )
        )
        return package.version

    def capture(self):
        return self._application().capture()

    def rescan(self):
        return self._application().rescan()

    def finish(self):
        application = self._application()
        application.ensure_can_finish()
        if self.runtime is None:  # Kept explicit for strict type narrowing.
            raise ApplianceStartupError("hardware application is not initialized")
        self.runtime.eject()
        return application.finish()

    def configure_camera(
        self,
        identifier: str,
        *,
        zoom: str | None = None,
        shutter: str | None = None,
    ):
        return self._application().configure_camera(
            identifier,
            zoom=zoom,
            shutter=shutter,
        )

    def test_capture(self, identifier: str):
        return self._application().test_capture(identifier)

    def inspect_detail(self, centre_x: float, centre_y: float):
        return self._application().inspect_detail(centre_x, centre_y)

    def save_debug_logs(self):
        return self._application().save_debug_logs()

    def cancel_startup(self) -> None:
        self.waiter.cancel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_startup()
        if self.runtime is None:
            return
        try:
            self.runtime.eject()
        except StorageError as error:
            # Shutdown has no caller left to retry, so report and continue.
            self.event_sink(
                ApplicationEvent(
                    EventKind.HARDWARE_WARNING,
                    f"Removable storage could not be ejected during shutdown: {error}",
                    {"component": "storage", "error": str(error)},
                )
            )

    def _application(self) -> PiScanApplication:
        if self._closed:
            raise ApplianceStartupError("hardware application is closed")
        if self.runtime is None:
            raise ApplianceStartupError("hardware application is not initialized")
        return self.runtime.application

    def _storage_status(self, status: StorageWaitStatus) -> None:
        details: dict[str, JsonValue] = {
            "state": status.state.value,
            "volume_count": status.volume_count,
        }
        if status.error is not None:
            details["error"] = status.error
        self.event_sink(ApplicationEvent(EventKind.STORAGE_STATUS, status.message, details))


def create_appliance(
    storage: RemovableStorage,
    *,
    selected_volume: StorageVolume | None = None,
    chdk_transport: ChdkPtpTransport | None = None,
    gphoto_transport: GphotoTransport | None = None,
    event_sink: EventSink = _ignore_event,
    minimum_free_bytes: int = 256 * 1024 * 1024,
    write_probe: Callable[[Path], None] = _verify_writable_storage,
    force_eject_after: float = 60.0,
    retry_interval: float = 1.0,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> ApplianceRuntime:
    """Mount one removable volume and construct the physical-camera application."""
    if selected_volume is None:
        volumes = storage.discover()
        if len(volumes) != 1:
            raise ApplianceStartupError(
                f"expected exactly one removable storage volume, found {len(volumes)}"
            )
        selected_volume = volumes[0]
    volume = storage.mount(selected_volume)
    try:
        if len(volume.mount_points) != 1:
            raise StorageError(
                f"mounted volume must have exactly one mount point: {volume.device_path}; "
                f"found {len(volume.mount_points)}"
            )
        if volume.read_only:
            raise StorageError(f"removable storage is read-only: {volume.device_path}")
        root = volume.mount_points[0]
        if not root.is_absolute():
            raise StorageError(f"removable storage mount point is not absolute: {root}")
        write_probe(root)
        image_directory = root / "images"
        configuration_path = root / "pi-scan.conf"
        diagnostics_path = root / "debug" / "events.jsonl"
        error_log_path = root / "debug" / "error.log"
        preview_directory = image_directory / ".pi-scan-previews"
        managed_paths = (
            image_directory,
            configuration_path,
            diagnostics_path.parent,
            diagnostics_path,
            error_log_path,
            preview_directory,
        )
        for managed_path in managed_paths:
            if managed_path.is_symlink():
                raise StorageError(
                    f"managed removable-media path is a symbolic link: {managed_path}"
                )
        managed_directories = (image_directory, diagnostics_path.parent, preview_directory)
        for managed_directory in managed_directories:
            if managed_directory.exists() and not managed_directory.is_dir():
                raise StorageError(
                    f"managed removable-media path is not a directory: {managed_directory}"
                )
            managed_directory.mkdir(parents=True, exist_ok=True)
            if not managed_directory.is_dir():
                raise StorageError(
                    f"managed removable-media path is not a directory: {managed_directory}"
                )
        for managed_file in (configuration_path, diagnostics_path, error_log_path):
            if managed_file.exists() and not managed_file.is_file():
                raise StorageError(
                    f"managed removable-media path is not a regular file: {managed_file}"
                )
        configuration = (
            load_legacy_configuration(configuration_path)
            if configuration_path.exists()
            else ScannerConfiguration({})
        )
        chdk = chdk_transport or ChdkPtpTransport()
        events = EventFanout(
            (
                event_sink,
                JsonLineEventLog(diagnostics_path),
                TextEventLog(error_log_path),
            )
        )
        discovery = PhysicalCameraDiscovery(
            configuration,
            chdk_transport=chdk,
            gphoto_transport=gphoto_transport,
            event_sink=events,
        )
        capacity_guard = StorageCapacityGuard(
            root,
            reserve_bytes=minimum_free_bytes,
        )
        application = PiScanApplication(
            chdk,
            image_directory,
            configuration,
            event_sink=events,
            session_camera_discovery=discovery.discover,
            configuration_path=configuration_path,
            before_capture=capacity_guard.check,
            diagnostics_directory=diagnostics_path.parent,
            preview_generator=PreviewGenerator(preview_directory),
        )
        update_package = find_update(root, __version__)
        if update_package is not None:
            events(
                ApplicationEvent(
                    EventKind.UPDATE_AVAILABLE,
                    f"Pi Scan {update_package.version} is available on the scan media",
                    {
                        "version": update_package.version,
                        "path": str(update_package.path),
                    },
                )
            )
        return ApplianceRuntime(
            application,
            storage,
            volume,
            configuration_path,
            diagnostics_path,
            update_package=update_package,
            force_eject_after=force_eject_after,
            retry_interval=retry_interval,
            clock=clock,
            sleeper=sleeper,
        )
    except Exception:
        with suppress(StorageError):
            storage.eject(volume)
        raise
