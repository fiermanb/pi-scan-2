from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_scan.cameras.chdk.camera import (
    ChdkCamera,
    ChdkCaptureSettings,
    calculate_zoom_step,
    capture_settings_from_legacy,
    parse_remote_integer,
    quote_cli_argument,
)
from pi_scan.cameras.chdk.errors import ChdkCaptureError, ChdkDiagnosticError
from pi_scan.cameras.chdk.models import ChdkDevice
from pi_scan.cameras.chdk.transport import ChdkProcessResult
from pi_scan.domain.configuration import CameraConfiguration


class RecordingTransport:
    def __init__(
        self,
        *,
        create_suffixes: tuple[str, ...] = (".JPG",),
        stdout: str = "",
    ) -> None:
        self.calls = []
        self.create_suffixes = create_suffixes
        self.stdout = stdout

    def run(self, commands, *, connection=None):
        self.calls.append((commands, connection))
        if commands and commands[-1].startswith("rs "):
            quoted_path = commands[-1].split('"')[1]
            base = Path(quoted_path)
            for suffix in self.create_suffixes:
                base.with_name(base.name + suffix).write_bytes(b"camera-data")
        return ChdkProcessResult(("chdkptp",), self.stdout, "")


def make_device() -> ChdkDevice:
    return ChdkDevice(
        index=1,
        model="Canon PowerShot A2500",
        bus="bus-0",
        device="device-1",
        vendor_id="0x4a9",
        product_id="0x3259",
        serial_number="serial-1",
        status="",
    )


class ChdkCameraTests(TestCase):
    def test_downloads_romlog_atomically_and_cleans_camera_file(self) -> None:
        class RomlogTransport(RecordingTransport):
            def run(self, commands, *, connection=None):
                self.calls.append((commands, connection))
                if commands[0].startswith("d ROMLOG.LOG "):
                    target = Path(commands[0].split('"')[1])
                    target.write_bytes(b"camera crash log")
                return ChdkProcessResult(("chdkptp",), "", "")

        transport = RomlogTransport()
        camera = ChdkCamera(make_device(), transport)
        with TemporaryDirectory() as directory:
            target = Path(directory) / "debug" / "odd-rom.log"
            self.assertEqual(camera.download_romlog(target), target)
            self.assertEqual(target.read_bytes(), b"camera crash log")
            self.assertEqual(
                [path for path in target.parent.iterdir() if path.name.startswith(".")],
                [],
            )
        self.assertIn("GetLogToFile", transport.calls[0][0][0])
        self.assertTrue(transport.calls[-1][0][0].startswith("=if os.stat"))

    def test_romlog_success_without_downloaded_file_is_typed_error(self) -> None:
        camera = ChdkCamera(make_device(), RecordingTransport())
        with TemporaryDirectory() as directory, self.assertRaises(ChdkDiagnosticError):
            camera.download_romlog(Path(directory) / "rom.log")

    def test_romlog_cleanup_failure_does_not_mask_download_failure(self) -> None:
        class FailingCleanupTransport(RecordingTransport):
            def run(self, commands, *, connection=None):
                if commands[0].startswith("=if os.stat"):
                    raise RuntimeError("camera cleanup failed")
                return super().run(commands, connection=connection)

        camera = ChdkCamera(make_device(), FailingCleanupTransport())
        with (
            TemporaryDirectory() as directory,
            self.assertRaisesRegex(ChdkDiagnosticError, "produced no camera ROM log"),
        ):
            camera.download_romlog(Path(directory) / "rom.log")

    def test_remote_capture_uses_documented_remoteshoot_command(self) -> None:
        transport = RecordingTransport()
        camera = ChdkCamera(make_device(), transport)
        with TemporaryDirectory() as directory:
            outputs = camera.capture(Path(directory) / "even")

        self.assertEqual(outputs[0].suffix, ".JPG")
        commands, connection = transport.calls[0]
        self.assertEqual(commands[0], "rec")
        # Pi Scan 1.5 passed svm = util.iso_to_sv96(100), which is 480 in APEX96.
        self.assertIn("-jpg -tv=1/15 -svm=480", commands[1])
        self.assertEqual(connection, make_device().connection)

    def test_can_request_jpeg_and_dng(self) -> None:
        transport = RecordingTransport(create_suffixes=(".JPG", ".DNG"))
        camera = ChdkCamera(
            make_device(),
            transport,
            settings=ChdkCaptureSettings(download_dng=True),
        )
        with TemporaryDirectory() as directory:
            outputs = camera.capture(Path(directory) / "odd")
        self.assertEqual({path.suffix for path in outputs}, {".JPG", ".DNG"})
        self.assertIn("-dng", transport.calls[0][0][1])

    def test_success_without_output_is_an_error(self) -> None:
        camera = ChdkCamera(make_device(), RecordingTransport(create_suffixes=()))
        with TemporaryDirectory() as directory, self.assertRaises(ChdkCaptureError):
            camera.capture(Path(directory) / "odd")

    def test_focus_and_zoom_are_scoped_to_selected_device(self) -> None:
        transport = RecordingTransport()
        camera = ChdkCamera(make_device(), transport)
        camera.set_focus_lock(True)
        camera.set_zoom(7)
        self.assertEqual(transport.calls[0][0], ["=set_aflock(1); return true"])
        self.assertEqual(transport.calls[1][0], ["=set_zoom(7); return get_zoom()"])

    def test_prepare_applies_legacy_chdk_settings(self) -> None:
        transport = RecordingTransport(stdout="1:return:10\n")
        camera = ChdkCamera(make_device(), transport)
        step = camera.prepare()
        self.assertEqual(step, 4)
        self.assertEqual(transport.calls[0][0], ["rec", "=return get_zoom_steps()"])
        preparation_script = transport.calls[1][0][0]
        self.assertIn("set_capture_mode(2)", preparation_script)
        self.assertIn("set_zoom(4)", preparation_script)
        self.assertIn("set_nd_filter(2)", preparation_script)
        self.assertIn("set_prop(props.WB_MODE,3)", preparation_script)
        self.assertIn("set_prop(props.FLASH_MODE,2)", preparation_script)

    def test_autofocus_locks_after_half_press(self) -> None:
        transport = RecordingTransport()
        ChdkCamera(make_device(), transport).autofocus_and_lock()
        command = transport.calls[0][0][0]
        self.assertIn("set_aflock(0)", command)
        self.assertIn("press('shoot_half')", command)
        self.assertIn("set_aflock(1)", command)

    def test_migrated_settings_feed_chdk_capture(self) -> None:
        settings = capture_settings_from_legacy(CameraConfiguration(zoom="7.5", shutter="1/30"))
        self.assertEqual(settings.zoom, "7.5")
        self.assertEqual(settings.shutter, "1/30")

    def test_settings_can_be_replaced_before_preparation(self) -> None:
        camera = ChdkCamera(make_device(), RecordingTransport())
        camera.configure(ChdkCaptureSettings(zoom="8", shutter="1/30"))
        self.assertEqual((camera.settings.zoom, camera.settings.shutter), ("8", "1/30"))

    def test_parses_documented_remote_integer_result(self) -> None:
        self.assertEqual(parse_remote_integer("1:return:number:42\n"), 42)
        self.assertEqual(calculate_zoom_step(10, "Max Zoom"), 9)
        self.assertEqual(calculate_zoom_step(10, "Min Zoom"), 0)

    def test_cli_quoting_rejects_control_characters(self) -> None:
        self.assertEqual(quote_cli_argument("a path"), '"a path"')
        with self.assertRaises(ValueError):
            quote_cli_argument("bad\npath")
