"""Behaviours restored from Pi Scan 1.5 after the rewrite was verified against it."""

import re
import zipfile
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest import TestCase

from pi_scan.cameras.chdk.camera import (
    ChdkCamera,
    ChdkCaptureSettings,
    calculate_zoom_step,
    iso_to_sv96,
)
from pi_scan.cameras.chdk.models import ChdkDevice
from pi_scan.commands import ApplicationCommand
from pi_scan.diagnostics import TextEventLog
from pi_scan.events import ApplicationEvent, EventKind
from pi_scan.input import InputController
from pi_scan.preview import _detail_box
from pi_scan.storage.removable import _parse_volume
from pi_scan.update import UpdateError, UpdatePackage, apply_update, find_update
from pi_scan.viewmodel import PreviewViewport, ScannerViewState, UiScreen


class TransportSpy:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, commands, *, connection=None):
        del connection
        self.commands.append(list(commands))

        class Result:
            stdout = "1:return:number:64\n"
            stderr = ""

        return Result()


def a_device() -> ChdkDevice:
    return ChdkDevice(
        index=1,
        model="Canon A2500",
        bus="bus-0",
        device="device-1",
        vendor_id="0x4a9",
        product_id="0x3259",
        serial_number="canon-1",
        status="",
    )


class ExposureTests(TestCase):
    """D1: 1.5 passed svm = util.iso_to_sv96(iso), not a raw market ISO."""

    def test_iso_100_converts_to_the_apex96_value_used_by_chdkptp(self) -> None:
        self.assertEqual(iso_to_sv96(100), 480)
        self.assertEqual(iso_to_sv96(200), 576)
        self.assertEqual(iso_to_sv96(50), 384)

    def test_capture_sends_the_converted_speed_value(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "scan"

            class CapturingTransport(TransportSpy):
                def run(self, commands, *, connection=None):
                    result = super().run(commands, connection=connection)
                    if any(command.startswith("rs ") for command in commands):
                        base.with_suffix(".jpg").write_bytes(b"jpeg")
                    return result

            transport = CapturingTransport()
            camera = ChdkCamera(a_device(), transport, settings=ChdkCaptureSettings())
            camera.capture(base)

        self.assertIn("-svm=480", transport.commands[-1][-1])
        self.assertNotIn("-sv=100", transport.commands[-1][-1])


class ZoomRoundingTests(TestCase):
    """D3: Python 2 rounded halves away from zero, so mid zoom differed by a step."""

    def test_half_steps_round_up_as_python_2_did(self) -> None:
        self.assertEqual(calculate_zoom_step(5, "5"), 2)
        self.assertEqual(calculate_zoom_step(9, "5"), 4)

    def test_other_values_are_unchanged(self) -> None:
        self.assertEqual(calculate_zoom_step(128, "5"), 63)
        self.assertEqual(calculate_zoom_step(10, "Min Zoom"), 0)
        self.assertEqual(calculate_zoom_step(10, "Max Zoom"), 9)


class CameraSignalTests(TestCase):
    """D4: 1.5 beeped on every failure and could power the cameras down."""

    def test_failure_beep_plays_the_legacy_sound(self) -> None:
        transport = TransportSpy()
        ChdkCamera(a_device(), transport).beep_failure()
        self.assertIn("play_sound(6)", transport.commands[0][0])

    def test_power_off_presses_the_camera_power_button(self) -> None:
        transport = TransportSpy()
        ChdkCamera(a_device(), transport).power_off()
        self.assertIn("PressPowerButton", transport.commands[0][0])


class StorageSelectionTests(TestCase):
    """B3: 1.5 accepted USB drives only, which excluded the appliance's own disk."""

    @staticmethod
    def row(**overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "path": "/dev/sda1",
            "type": "part",
            "tran": "usb",
            "rm": True,
            "mountpoints": ["/media/pi/SCANS"],
            "fstype": "exfat",
            "label": "SCANS",
            "uuid": "1234-ABCD",
            "size": 64_000_000_000,
            "fsavail": 32_000_000_000,
            "ro": False,
        }
        row.update(overrides)
        return row

    def test_usb_scan_media_is_accepted(self) -> None:
        volume = _parse_volume(self.row())
        assert volume is not None
        self.assertEqual(volume.device_path, Path("/dev/sda1"))

    def test_non_usb_removable_media_is_rejected(self) -> None:
        """The appliance's own SD card reports rm=1 on some kernels."""
        self.assertIsNone(_parse_volume(self.row(path="/dev/mmcblk0p2", tran=None, rm=True)))

    def test_system_mount_points_are_rejected_even_over_usb(self) -> None:
        for mount in ("/", "/boot", "/boot/firmware", "/home"):
            with self.subTest(mount=mount):
                self.assertIsNone(_parse_volume(self.row(mountpoints=[mount])))


class KeypadTests(TestCase):
    """B1: 1.5 was driven from a numeric keypad, one digit map per screen."""

    class FakeViewModel:
        def __init__(self, state: ScannerViewState) -> None:
            self.state = state
            self.commands: list[ApplicationCommand] = []

        def dispatch(self, command):
            self.commands.append(command)

        def zoom_preview(self, factor):
            self.state = replace(self.state, viewport=self.state.viewport.zoomed(factor))

        def pan_preview(self, x, y):
            self.state = replace(self.state, viewport=self.state.viewport.panned(x, y))

        def reset_preview(self):
            self.state = replace(self.state, viewport=PreviewViewport())

    def dispatched(self, key: str, **state: object) -> list[ApplicationCommand]:
        view_model = self.FakeViewModel(ScannerViewState(**state))  # type: ignore[arg-type]
        InputController().handle_key(key, view_model)
        return view_model.commands

    def test_one_runs_the_primary_action_of_each_screen(self) -> None:
        cases = (
            (UiScreen.PREPARATION, "can_prepare", ApplicationCommand.PREPARE),
            (UiScreen.FOCUS_CONFIRMATION, "can_focus", ApplicationCommand.FOCUS),
            (UiScreen.ERROR, "can_recover", ApplicationCommand.RECOVER),
        )
        for screen, capability, command in cases:
            with self.subTest(screen=screen):
                self.assertEqual(
                    self.dispatched("1", screen=screen, **{capability: True}),
                    [command],
                )

    def test_three_and_five_run_the_secondary_actions(self) -> None:
        self.assertEqual(
            self.dispatched("3", screen=UiScreen.CAPTURE, can_rescan=True),
            [ApplicationCommand.RESCAN],
        )
        self.assertEqual(
            self.dispatched("5", screen=UiScreen.CAPTURE, can_finish=True),
            [ApplicationCommand.FINISH],
        )
        self.assertEqual(
            self.dispatched("5", screen=UiScreen.FOCUS_CONFIRMATION, can_swap=True),
            [ApplicationCommand.SWAP_CAMERAS],
        )

    def test_no_digit_captures_a_page(self) -> None:
        """1.5 kept capture on the pedal and B/C/space so a keypress could not shoot."""
        for digit in "0123456789":
            with self.subTest(digit=digit):
                self.assertNotIn(
                    ApplicationCommand.CAPTURE,
                    self.dispatched(digit, screen=UiScreen.CAPTURE, can_capture=True),
                )

    def test_pan_digits_work_where_the_screen_does_not_claim_them(self) -> None:
        view_model = self.FakeViewModel(ScannerViewState(screen=UiScreen.CAPTURE))
        controller = InputController()
        self.assertTrue(controller.handle_key("8", view_model))
        self.assertTrue(controller.handle_key("4", view_model))
        self.assertAlmostEqual(view_model.state.viewport.offset_y, 0.1)
        self.assertAlmostEqual(view_model.state.viewport.offset_x, -0.1)

    def test_a_claimed_digit_beats_panning(self) -> None:
        self.assertEqual(
            self.dispatched("2", screen=UiScreen.COMPLETE, can_turn_off_cameras=True),
            [ApplicationCommand.TURN_OFF_CAMERAS],
        )

    def test_digits_respect_the_enabled_state(self) -> None:
        self.assertEqual(self.dispatched("1", screen=UiScreen.PREPARATION), [])
        self.assertEqual(self.dispatched("3", screen=UiScreen.CAPTURE), [])


class DetailWindowTests(TestCase):
    """B2: 1.5 inspected focus on native pixels, not on a downsampled preview."""

    def test_window_is_centred_on_the_viewport(self) -> None:
        self.assertEqual(_detail_box((4000, 3000), (1600, 1200), 0.0, 0.0), (1200, 900, 2800, 2100))

    def test_window_is_clamped_inside_the_page(self) -> None:
        self.assertEqual(_detail_box((4000, 3000), (1600, 1200), -1.0, 1.0), (0, 0, 1600, 1200))
        self.assertEqual(
            _detail_box((4000, 3000), (1600, 1200), 1.0, -1.0), (2400, 1800, 4000, 3000)
        )

    def test_small_pages_are_not_padded(self) -> None:
        self.assertEqual(_detail_box((800, 600), (1600, 1200), 0.0, 0.0), (0, 0, 800, 600))


class ErrorLogTests(TestCase):
    """D4: field staff read debug/error.log directly off the scan media."""

    def test_events_are_appended_in_the_legacy_line_format(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug" / "error.log"
            log = TextEventLog(path)
            log(ApplicationEvent(EventKind.OPERATION_STARTED, "Capture started"))
            log(
                ApplicationEvent(
                    EventKind.OPERATION_FAILED,
                    "Capture failed",
                    {"error": "odd camera returned no JPEG"},
                )
            )
            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} -- ")
        self.assertTrue(lines[0].endswith("Capture started"))
        self.assertTrue(lines[1].endswith("Capture failed: odd camera returned no JPEG"))


class MediaUpdateTests(TestCase):
    """D4: 1.5 installed pi-scan-update-X.Y.archive from the scan media."""

    def make_media(self, root: Path, *names: str) -> None:
        """Each archive carries the Pi Scan wheel whose version its name advertises."""
        for name in names:
            advertised = re.fullmatch(r"pi-scan-update-([0-9]+\.[0-9]+)\.archive", name)
            member = (
                f"pi_scan-{advertised.group(1)}.0-py3-none-any.whl"
                if advertised is not None
                else "read-me.txt"
            )
            with zipfile.ZipFile(root / name, "w") as bundle:
                bundle.writestr(member, "wheel")

    def test_only_newer_versions_are_offered(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_media(
                root,
                "pi-scan-update-1.5.archive",
                "pi-scan-update-2.0.archive",
                "pi-scan-update-2.1.archive",
                "pi-scan-update-3.0.archive",
                "not-an-update.archive",
            )
            package = find_update(root, "2.0.0")

        assert package is not None
        self.assertEqual(package.version, "3.0")

    def test_no_update_is_reported_when_none_is_newer(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_media(root, "pi-scan-update-1.5.archive")
            self.assertIsNone(find_update(root, "2.0.0"))

    def test_applying_installs_the_wheel_into_the_running_environment(self) -> None:
        import tempfile

        commands: list[list[str]] = []

        def runner(command):
            commands.append(list(command))
            return CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_media(root, "pi-scan-update-3.0.archive")
            package = UpdatePackage(root / "pi-scan-update-3.0.archive", 3, 0)
            installed = apply_update(
                package, python_executable="/opt/pi-scan/venv/bin/python", runner=runner
            )

        self.assertEqual(installed, "pi_scan-3.0.0-py3-none-any.whl")
        self.assertEqual(
            commands[0][:5], ["/opt/pi-scan/venv/bin/python", "-m", "pip", "install", "--no-index"]
        )

    def test_an_archive_without_exactly_one_wheel_is_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "pi-scan-update-3.0.archive"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("readme.txt", "no wheel here")
            with self.assertRaisesRegex(UpdateError, "exactly one wheel"):
                apply_update(UpdatePackage(archive, 3, 0), runner=lambda command: None)


class FailingTransport(TransportSpy):
    """Answers preparation queries but never produces a captured file."""


def chdk_pair(transport):
    return [
        ChdkCamera(replace_device(a_device(), 1, "odd-serial"), transport),
        ChdkCamera(replace_device(a_device(), 2, "even-serial"), transport),
    ]


def replace_device(device: ChdkDevice, index: int, serial: str) -> ChdkDevice:
    return ChdkDevice(
        index=index,
        model=device.model,
        bus=device.bus,
        device=f"device-{index}",
        vendor_id=device.vendor_id,
        product_id=device.product_id,
        serial_number=serial,
        status=device.status,
    )


class ApplicationSignalTests(TestCase):
    """D4 at the application level: the failure tone and the power-off control."""

    def make_application(self, directory: str, transport):
        from pi_scan.application import PiScanApplication
        from pi_scan.cameras.chdk.transport import ChdkPtpTransport
        from pi_scan.domain.configuration import ScannerConfiguration

        cameras = chdk_pair(transport)
        events: list[ApplicationEvent] = []
        application = PiScanApplication(
            ChdkPtpTransport(),
            Path(directory),
            ScannerConfiguration({}),
            event_sink=events.append,
            session_camera_discovery=lambda: cameras,
        )
        return application, events

    def test_a_failed_capture_sounds_both_cameras(self) -> None:
        import tempfile

        from pi_scan.domain.capture import CapturePairError

        transport = FailingTransport()
        with tempfile.TemporaryDirectory() as directory:
            application, _ = self.make_application(directory, transport)
            application.initialize()
            application.prepare()
            application.focus()
            with self.assertRaises(CapturePairError):
                application.capture()

        beeps = [command for command in transport.commands if "play_sound(6)" in command[0]]
        self.assertEqual(len(beeps), 2)

    def test_cameras_can_be_turned_off_before_scanning(self) -> None:
        import tempfile

        transport = TransportSpy()
        with tempfile.TemporaryDirectory() as directory:
            application, _ = self.make_application(directory, transport)
            application.initialize()
            turned_off = application.turn_off_cameras()

        self.assertEqual(set(turned_off), {"odd-serial", "even-serial"})
        presses = [c for c in transport.commands if "PressPowerButton" in c[0]]
        self.assertEqual(len(presses), 2)

    def test_cameras_cannot_be_turned_off_mid_session(self) -> None:
        import tempfile

        transport = TransportSpy()
        with tempfile.TemporaryDirectory() as directory:
            application, _ = self.make_application(directory, transport)
            application.initialize()
            application.prepare()
            with self.assertRaisesRegex(RuntimeError, "before or after scanning"):
                application.turn_off_cameras()


class DetailInspectionTests(TestCase):
    """B2 end to end: the inspected window carries native pixels, not a thumbnail."""

    def test_inspecting_a_capture_returns_an_unscaled_window(self) -> None:
        import io
        import tempfile

        from PIL import Image

        from pi_scan.application import PiScanApplication
        from pi_scan.cameras.chdk.transport import ChdkPtpTransport
        from pi_scan.cameras.fake import FakeCamera
        from pi_scan.domain.configuration import ScannerConfiguration

        buffer = io.BytesIO()
        Image.new("RGB", (3000, 2000), "white").save(buffer, format="JPEG")
        page = buffer.getvalue()

        class PageCamera(FakeCamera):
            def probe(self) -> None:
                pass

            def prepare(self) -> None:
                pass

            def autofocus_and_lock(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            cameras = [
                PageCamera("odd-serial", files={".jpg": page}),
                PageCamera("even-serial", files={".jpg": page}),
            ]
            events: list[ApplicationEvent] = []
            application = PiScanApplication(
                ChdkPtpTransport(),
                Path(directory),
                ScannerConfiguration({}),
                event_sink=events.append,
                session_camera_discovery=lambda: cameras,
            )
            application.initialize()
            application.prepare()
            application.focus()
            application.capture()
            detail = application.inspect_detail(0.0, 0.0)

            self.assertEqual((detail.even.width, detail.even.height), (1600, 1200))
            self.assertTrue(detail.even.path.is_file())
            self.assertTrue(detail.odd.path.is_file())

        kinds = [event.kind for event in events]
        self.assertIn(EventKind.DETAIL_READY, kinds)


class UpdateFailureTests(TestCase):
    def test_an_unreadable_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateError, "unreadable application version"):
            find_update(Path("."), "not-a-version")

    def test_a_missing_media_root_offers_nothing(self) -> None:
        self.assertIsNone(find_update(Path("no-such-directory"), "2.0.0"))

    def test_a_corrupt_archive_is_reported_clearly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "pi-scan-update-3.0.archive"
            archive.write_bytes(b"not a zip file")
            with self.assertRaisesRegex(UpdateError, "could not read"):
                apply_update(UpdatePackage(archive, 3, 0), runner=lambda command: None)

    def test_a_failing_installation_is_reported_with_its_diagnostics(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "pi-scan-update-3.0.archive"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("pi_scan-3.0.0-py3-none-any.whl", "wheel")

            def runner(command):
                return CompletedProcess(command, 1, "", "no matching distribution")

            with self.assertRaisesRegex(UpdateError, "no matching distribution"):
                apply_update(UpdatePackage(archive, 3, 0), runner=runner)


class ViewStateTests(TestCase):
    """The operator's controls follow the state the appliance reports."""

    def apply(self, *events: ApplicationEvent):
        from pi_scan.viewmodel import ScannerViewModel, UiEventBridge

        bridge = UiEventBridge()

        class Runner:
            busy = False
            application = None

        view_model = ScannerViewModel(Runner(), bridge)  # type: ignore[arg-type]
        for event in events:
            bridge(event)
        return view_model.poll()

    def test_an_available_update_enables_the_update_control(self) -> None:
        state = self.apply(
            ApplicationEvent(
                EventKind.UPDATE_AVAILABLE,
                "Pi Scan 3.0 is available on the scan media",
                {"version": "3.0"},
            ),
            ApplicationEvent(EventKind.STATE_CHANGED, "state", {"state": "new"}),
        )
        self.assertEqual(state.update_version, "3.0")
        self.assertTrue(state.can_apply_update)

    def test_applying_an_update_clears_the_offer(self) -> None:
        state = self.apply(
            ApplicationEvent(EventKind.UPDATE_AVAILABLE, "available", {"version": "3.0"}),
            ApplicationEvent(EventKind.UPDATE_APPLIED, "installed", {"version": "3.0"}),
        )
        self.assertIsNone(state.update_version)

    def test_turning_the_cameras_off_needs_a_chdk_camera_and_a_quiet_session(self) -> None:
        assigned = ApplicationEvent(
            EventKind.CAMERAS_ASSIGNED,
            "assigned",
            {"even_camera": "a", "odd_camera": "b", "even_backend": "chdk", "odd_backend": "chdk"},
        )
        ready = self.apply(
            assigned, ApplicationEvent(EventKind.STATE_CHANGED, "state", {"state": "ready"})
        )
        self.assertFalse(ready.can_turn_off_cameras)
        complete = self.apply(
            assigned, ApplicationEvent(EventKind.STATE_CHANGED, "state", {"state": "complete"})
        )
        self.assertTrue(complete.can_turn_off_cameras)

    def test_a_new_capture_replaces_any_detail_window(self) -> None:
        state = self.apply(
            ApplicationEvent(
                EventKind.DETAIL_READY,
                "detail",
                {"even_detail": "even.jpg", "odd_detail": "odd.jpg"},
            ),
            ApplicationEvent(
                EventKind.CAPTURE_SUCCEEDED,
                "captured",
                {"even_page": 2, "odd_page": 3},
            ),
        )
        self.assertIsNone(state.even_detail)
        self.assertIsNone(state.odd_detail)


class FailureScreenTests(TestCase):
    """1.5 kept capture off the failure screen and continued without re-preparing."""

    def state(self, **overrides):
        from pi_scan.viewmodel import ScannerViewModel, UiEventBridge

        bridge = UiEventBridge()

        class Runner:
            busy = False
            application = None

        view_model = ScannerViewModel(Runner(), bridge)  # type: ignore[arg-type]
        bridge(
            ApplicationEvent(
                EventKind.STATE_CHANGED, "state", {"state": overrides.pop("scanner_state")}
            )
        )
        return view_model.poll()

    def test_capture_is_not_offered_while_a_failure_is_displayed(self) -> None:
        failed = self.state(scanner_state="capture_failed")
        self.assertFalse(failed.can_capture)
        self.assertTrue(failed.can_dismiss_failure)
        self.assertTrue(self.state(scanner_state="ready").can_capture)

    def test_the_pedal_does_not_fire_on_the_failure_screen(self) -> None:
        controller = InputController()
        view_model = KeypadTests.FakeViewModel(self.state(scanner_state="capture_failed"))
        controller.handle_pedal_level(1, view_model)
        self.assertFalse(controller.handle_pedal_level(0, view_model))
        self.assertEqual(view_model.commands, [])

    def test_capture_keys_do_not_fire_on_the_failure_screen(self) -> None:
        for key in (" ", "b", "c"):
            with self.subTest(key=key):
                view_model = KeypadTests.FakeViewModel(self.state(scanner_state="capture_failed"))
                InputController().handle_key(key, view_model)
                self.assertEqual(view_model.commands, [])

    def test_one_continues_from_a_failed_capture_without_re_preparing(self) -> None:
        view_model = KeypadTests.FakeViewModel(self.state(scanner_state="capture_failed"))
        InputController().handle_key("1", view_model)
        self.assertEqual(view_model.commands, [ApplicationCommand.DISMISS_FAILURE])

    def test_one_recovers_when_preparation_itself_failed(self) -> None:
        view_model = KeypadTests.FakeViewModel(self.state(scanner_state="failed"))
        InputController().handle_key("1", view_model)
        self.assertEqual(view_model.commands, [ApplicationCommand.RECOVER])

    def test_dismissing_returns_to_scanning_without_touching_the_cameras(self) -> None:
        import tempfile

        from pi_scan.domain.capture import CapturePairError
        from pi_scan.domain.session import SessionState

        transport = FailingTransport()
        with tempfile.TemporaryDirectory() as directory:
            application, _ = ApplicationSignalTests().make_application(directory, transport)
            session = application.initialize()
            application.prepare()
            application.focus()
            with self.assertRaises(CapturePairError):
                application.capture()
            self.assertEqual(session.state, SessionState.CAPTURE_FAILED)

            issued = len(transport.commands)
            application.dismiss_failure()

        self.assertEqual(session.state, SessionState.READY)
        self.assertEqual(len(transport.commands), issued)


class InputProfileTests(TestCase):
    """1.5 shipped two images differing only in their Kivy input configuration."""

    class FakeConfig:
        def __init__(self) -> None:
            self.settings: dict[tuple[str, str], object] = {}
            self.removed: list[tuple[str, str]] = []

        def set(self, section: str, option: str, value: object) -> None:
            self.settings[(section, option)] = value

        def remove_option(self, section: str, option: str) -> None:
            self.removed.append((section, option))

    def applied(self, name: str | None):
        from pi_scan.ui.input_profiles import apply_input_profile, select_profile

        config = self.FakeConfig()
        apply_input_profile(select_profile(name), config)
        return config

    def test_mouse_profile_matches_the_1_5_mouse_image(self) -> None:
        config = self.applied("mouse")
        self.assertEqual(config.settings[("input", "mouse")], "mouse")
        self.assertEqual(config.settings[("input", "%(name)s")], "probesysfs,provider=hidinput")
        self.assertEqual(config.settings[("kivy", "keyboard_mode")], "")
        self.assertEqual(config.settings[("modules", "touchring")], "show_cursor=true")
        self.assertNotIn(("input", "mtdev_%(name)s"), config.settings)

    def test_touch_profile_matches_the_1_5_touch_image(self) -> None:
        config = self.applied("touch")
        self.assertEqual(config.settings[("input", "mtdev_%(name)s")], "probesysfs,provider=mtdev")
        self.assertEqual(config.settings[("input", "hid_%(name)s")], "probesysfs,provider=hidinput")
        self.assertEqual(config.settings[("kivy", "keyboard_mode")], "system")
        self.assertIn(("modules", "touchring"), config.removed)
        self.assertNotIn(("modules", "touchring"), config.settings)

    def test_both_profiles_name_hidinput_rather_than_trusting_kivy_detection(self) -> None:
        """Kivy adds provider=hidinput only if /opt/vc/include/bcm_host.h exists."""
        for name in ("mouse", "touch"):
            with self.subTest(profile=name):
                config = self.applied(name)
                self.assertEqual(
                    config.settings[("input", "%(name)s")], "probesysfs,provider=hidinput"
                )

    def test_the_default_is_the_mouse_profile(self) -> None:
        self.assertEqual(self.applied(None).settings, self.applied("mouse").settings)

    def test_an_unknown_profile_is_refused(self) -> None:
        from pi_scan.ui.input_profiles import select_profile

        with self.assertRaisesRegex(ValueError, "unknown input profile"):
            select_profile("trackball")

    def test_the_deployment_records_the_choice(self) -> None:
        environment = (Path(__file__).parents[1] / "deploy" / "pi-scan.env").read_text(
            encoding="utf-8"
        )
        self.assertIn("PI_SCAN_INPUT=mouse", environment)
