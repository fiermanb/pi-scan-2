from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from pi_scan.application import PiScanApplication
from pi_scan.cameras.chdk.camera import ChdkCamera, ChdkCaptureSettings
from pi_scan.cameras.chdk.models import ChdkDevice
from pi_scan.cameras.chdk.transport import ChdkPtpTransport
from pi_scan.cameras.fake import FakeCamera
from pi_scan.domain.capture import CapturePairError
from pi_scan.domain.configuration import (
    CameraConfiguration,
    CameraSide,
    ScannerConfiguration,
    load_legacy_configuration,
)
from pi_scan.domain.session import SessionOperationError, SessionState
from pi_scan.events import EventKind


class ApplicationCamera(FakeCamera):
    def __init__(self, identifier: str) -> None:
        super().__init__(identifier)
        self.prepare_count = 0
        self.focus_count = 0

    def probe(self) -> None:
        pass

    def prepare(self) -> None:
        self.prepare_count += 1

    def autofocus_and_lock(self) -> None:
        self.focus_count += 1


def device(identifier: str, index: int) -> ChdkDevice:
    return ChdkDevice(
        index=index,
        model=f"Canon {identifier}",
        bus="bus-0",
        device=f"device-{index}",
        vendor_id="0x4a9",
        product_id="0x3259",
        serial_number=identifier,
        status="",
    )


class ApplicationTests(TestCase):
    def make_romlog_application(self, directory: str, *, failing_device: str | None = None):
        class RomlogTransport:
            def run(self, commands, *, connection=None):
                if commands[0].startswith("d ROMLOG.LOG "):
                    if connection is not None and connection.device == failing_device:
                        raise RuntimeError("camera disconnected")
                    target = Path(commands[0].split('"')[1])
                    target.write_bytes(f"log from {connection.device}".encode())
                return type("Result", (), {"stdout": "", "stderr": ""})()

        root = Path(directory)
        events = []
        transport = RomlogTransport()
        cameras = (
            ChdkCamera(device("one", 1), transport),
            ChdkCamera(device("two", 2), transport),
        )
        application = PiScanApplication(
            transport,
            root / "images",
            ScannerConfiguration({}),
            event_sink=events.append,
            session_camera_discovery=lambda: cameras,
            diagnostics_directory=root / "debug",
        )
        application.initialize()
        return application, events

    def test_saves_and_reports_both_chdk_debug_logs(self) -> None:
        with TemporaryDirectory() as directory:
            application, events = self.make_romlog_application(directory)
            saved = application.save_debug_logs()
            self.assertEqual({path.name for path in saved}, {"even-rom.log", "odd-rom.log"})
            self.assertTrue(all(path.read_bytes().startswith(b"log from") for path in saved))
            self.assertEqual(
                len([event for event in events if event.kind is EventKind.DEBUG_LOG_SAVED]),
                2,
            )

    def test_reports_successful_debug_log_when_other_camera_fails(self) -> None:
        with TemporaryDirectory() as directory:
            application, events = self.make_romlog_application(
                directory,
                failing_device="device-2",
            )
            with self.assertRaises(SessionOperationError):
                application.save_debug_logs()
            self.assertEqual(
                len([event for event in events if event.kind is EventKind.DEBUG_LOG_SAVED]),
                1,
            )
            failure = next(event for event in events if event.kind is EventKind.OPERATION_FAILED)
            self.assertEqual(failure.details["operation"], "debug_log")

    def test_chdk_test_capture_creates_preview_without_scan_page(self) -> None:
        class TestShotTransport:
            def run(self, commands, *, connection=None):
                if commands[-1] == "=return get_zoom_steps()":
                    return type("Result", (), {"stdout": "1:return:10\n", "stderr": ""})()
                if commands[-1].startswith("rs "):
                    base = Path(commands[-1].split('"')[1])
                    Image.new("RGB", (40, 20), color="white").save(base.with_suffix(".jpg"))
                return type("Result", (), {"stdout": "", "stderr": ""})()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            transport = TestShotTransport()
            application = PiScanApplication(
                transport,
                root / "images",
                ScannerConfiguration({}),
                event_sink=events.append,
                device_discovery=lambda selected: (device("one", 1), device("two", 2)),
            )
            application.initialize()
            preview = application.test_capture("one")
            self.assertTrue(preview.exists())
            self.assertEqual(list((root / "images").glob("[0-9]*.jpg")), [])
            self.assertEqual(
                list(application.preview_generator.directory.glob(".pi-scan-test-*")),
                [],
            )
            event = next(item for item in events if item.kind is EventKind.TEST_CAPTURE_SUCCEEDED)
            self.assertEqual(event.details["identifier"], "one")

    def make_application(self, directory: str):
        events = []
        made: dict[str, tuple[ApplicationCamera, ChdkCaptureSettings]] = {}

        def discovery(transport):
            return (device("odd-serial", 1), device("even-serial", 2))

        def factory(chdk_device, transport, settings):
            camera = ApplicationCamera(chdk_device.identity.identifier)
            made[chdk_device.identity.identifier] = (camera, settings)
            return camera

        configuration = ScannerConfiguration(
            {
                "odd-serial": CameraConfiguration(
                    position=CameraSide.ODD, zoom="7.5", shutter="1/30"
                ),
                "even-serial": CameraConfiguration(position=CameraSide.EVEN),
            }
        )
        application = PiScanApplication(
            ChdkPtpTransport(),
            Path(directory),
            configuration,
            event_sink=events.append,
            device_discovery=discovery,
            camera_factory=factory,
        )
        return application, events, made

    def test_initialization_builds_configured_camera_pair(self) -> None:
        with TemporaryDirectory() as directory:
            application, events, made = self.make_application(directory)
            session = application.initialize()
            self.assertEqual(session.cameras.odd.identity.identifier, "odd-serial")
            self.assertEqual(session.cameras.even.identity.identifier, "even-serial")
            odd_settings = made["odd-serial"][1]
            self.assertEqual((odd_settings.zoom, odd_settings.shutter), ("7.5", "1/30"))
            self.assertEqual(events[0].kind, EventKind.DISCOVERY_STARTED)
            self.assertEqual(events[1].kind, EventKind.CAMERAS_ASSIGNED)

    def test_initialization_can_accept_backend_independent_cameras(self) -> None:
        with TemporaryDirectory() as directory:
            cameras = [ApplicationCamera("one"), ApplicationCamera("two")]
            application = PiScanApplication(
                ChdkPtpTransport(),
                Path(directory),
                ScannerConfiguration({}),
                session_camera_discovery=lambda: cameras,
            )
            session = application.initialize()
            self.assertEqual(
                {session.cameras.even.identity.identifier, session.cameras.odd.identity.identifier},
                {"one", "two"},
            )

    def test_service_drives_complete_workflow_and_emits_capture(self) -> None:
        with TemporaryDirectory() as directory:
            application, events, _ = self.make_application(directory)
            application.initialize()
            application.prepare()
            application.focus()
            result = application.capture()
            self.assertEqual((result.even_page, result.odd_page), (0, 1))
            self.assertEqual(application.session.state, SessionState.READY)
            captures = [event for event in events if event.kind is EventKind.CAPTURE_SUCCEEDED]
            self.assertEqual(captures[0].details["even_page"], 0)

    def test_failure_event_contains_camera_diagnostics(self) -> None:
        with TemporaryDirectory() as directory:
            application, events, made = self.make_application(directory)
            application.initialize()
            application.prepare()
            application.focus()
            made["odd-serial"][0].failure = RuntimeError("disconnected")
            with self.assertRaises(CapturePairError):
                application.capture()
            failure = next(event for event in events if event.kind is EventKind.OPERATION_FAILED)
            self.assertEqual(failure.details["operation"], "capture")
            self.assertIn("odd", failure.details["camera_failures"])

    def test_capture_preflight_runs_before_either_camera(self) -> None:
        with TemporaryDirectory() as directory:
            application, events, made = self.make_application(directory)
            application.initialize()
            application.prepare()
            application.focus()
            application.before_capture = lambda: (_ for _ in ()).throw(RuntimeError("drive full"))
            with self.assertRaisesRegex(RuntimeError, "drive full"):
                application.capture()
            self.assertEqual(made["odd-serial"][0].capture_count, 0)
            self.assertEqual(made["even-serial"][0].capture_count, 0)
            failure = next(event for event in events if event.kind is EventKind.OPERATION_FAILED)
            self.assertEqual(failure.details["operation"], "capture")

    def test_missing_legacy_configuration_uses_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            application = PiScanApplication.from_legacy_paths(
                ChdkPtpTransport(),
                Path(directory) / "images",
                Path(directory) / "missing.conf",
            )
            self.assertEqual(application.configuration, ScannerConfiguration({}))

    def test_swap_updates_and_persists_camera_roles(self) -> None:
        with TemporaryDirectory() as directory:
            application, events, _ = self.make_application(directory)
            session = application.initialize()
            path = Path(directory) / "pi-scan.conf"
            application.swap_cameras(save_to=path)
            self.assertEqual(session.cameras.even.identity.identifier, "odd-serial")
            persisted = path.read_text(encoding="utf-8")
            self.assertIn('"position": "even"', persisted)
            self.assertEqual(events[-2].kind, EventKind.CAMERAS_ASSIGNED)

    def test_swap_uses_default_appliance_configuration_path(self) -> None:
        with TemporaryDirectory() as directory:
            application, _, _ = self.make_application(directory)
            path = Path(directory) / "pi-scan.conf"
            application.configuration_path = path
            application.initialize()
            application.swap_cameras()
            self.assertTrue(path.exists())

    def test_swap_persistence_failure_restores_camera_roles_and_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            application, _, _ = self.make_application(directory)
            session = application.initialize()
            original_pair = session.cameras
            original_configuration = application.configuration
            with (
                patch(
                    "pi_scan.application.save_legacy_configuration",
                    side_effect=OSError("media failed"),
                ),
                self.assertRaisesRegex(OSError, "media failed"),
            ):
                application.swap_cameras(save_to=Path(directory) / "pi-scan.conf")
            self.assertEqual(session.cameras, original_pair)
            self.assertEqual(application.configuration, original_configuration)

    def test_configure_chdk_camera_updates_runtime_and_persists(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            chdk_cameras = {}

            def factory(chdk_device, transport, settings):
                camera = ChdkCamera(chdk_device, transport, settings=settings)
                chdk_cameras[chdk_device.identity.identifier] = camera
                return camera

            application = PiScanApplication(
                ChdkPtpTransport(),
                Path(directory) / "images",
                ScannerConfiguration({}),
                device_discovery=lambda transport: (device("one", 1), device("two", 2)),
                camera_factory=factory,
                configuration_path=path,
            )
            application.initialize()
            selected = application.configure_camera("one", zoom="8", shutter="1/30")
            self.assertEqual((selected.zoom, selected.shutter), ("8", "1/30"))
            self.assertEqual(chdk_cameras["one"].settings.zoom, "8")
            self.assertEqual(load_legacy_configuration(path).camera("one"), selected)

    def test_setting_persistence_failure_restores_runtime_camera_settings(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            cameras = {}

            def factory(chdk_device, transport, settings):
                camera = ChdkCamera(chdk_device, transport, settings=settings)
                cameras[chdk_device.identity.identifier] = camera
                return camera

            application = PiScanApplication(
                ChdkPtpTransport(),
                Path(directory) / "images",
                ScannerConfiguration({}),
                device_discovery=lambda transport: (device("one", 1), device("two", 2)),
                camera_factory=factory,
                configuration_path=path,
            )
            application.initialize()
            original_configuration = application.configuration
            original_settings = cameras["one"].settings
            with (
                patch(
                    "pi_scan.application.save_legacy_configuration",
                    side_effect=OSError("media failed"),
                ),
                self.assertRaisesRegex(OSError, "media failed"),
            ):
                application.configure_camera("one", zoom="8", shutter="1/30")
            self.assertEqual(cameras["one"].settings, original_settings)
            self.assertEqual(application.configuration, original_configuration)

    def test_camera_settings_are_locked_after_preparation(self) -> None:
        with TemporaryDirectory() as directory:
            application, _, _ = self.make_application(directory)
            application.initialize()
            application.prepare()
            with self.assertRaisesRegex(RuntimeError, "before preparation"):
                application.configure_camera("odd-serial", zoom="8")
