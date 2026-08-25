import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_scan import __version__
from pi_scan.cameras.fake import SimulatedCamera, SimulatedCameraError
from pi_scan.events import EventKind
from pi_scan.simulator import create_simulator, main


class SimulatedCameraTests(TestCase):
    def test_requires_prepare_and_focus_before_capture(self) -> None:
        camera = SimulatedCamera("camera")
        with TemporaryDirectory() as directory:
            with self.assertRaises(SimulatedCameraError):
                camera.capture(Path(directory) / "page")
            camera.prepare()
            camera.autofocus_and_lock()
            outputs = camera.capture(Path(directory) / "page")
            self.assertEqual(outputs[0].read_bytes(), b"fake-jpeg-data")

    def test_reports_disconnection_during_probe(self) -> None:
        camera = SimulatedCamera("camera")
        camera.connected = False
        with self.assertRaisesRegex(SimulatedCameraError, "disconnected"):
            camera.probe()


class SimulatorEndToEndTests(TestCase):
    def test_cli_reports_installed_version_without_running_workflow(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"pi-scan-sim {__version__}")

    def test_cli_captures_multiple_jpeg_and_dng_pairs(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "images"
            result = main(["--output", str(output), "--pairs", "2", "--dng", "--quiet"])
            self.assertEqual(result, 0)
            self.assertEqual(
                {path.name for path in output.iterdir() if path.is_file()},
                {
                    "0000.jpg",
                    "0000.dng",
                    "0001.jpg",
                    "0001.dng",
                    "0002.jpg",
                    "0002.dng",
                    "0003.jpg",
                    "0003.dng",
                },
            )

    def test_simulator_continues_existing_page_numbers(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "0000.jpg").write_bytes(b"existing")
            (output / "0001.jpg").write_bytes(b"existing")
            self.assertEqual(main(["--output", directory, "--quiet"]), 0)
            self.assertTrue((output / "0002.jpg").exists())
            self.assertTrue((output / "0003.jpg").exists())

    def test_complete_workflow_emits_structured_events(self) -> None:
        with TemporaryDirectory() as directory:
            events = []
            application = create_simulator(Path(directory), event_sink=events.append)
            application.initialize()
            application.prepare()
            application.focus()
            application.capture()
            kinds = [event.kind for event in events]
            self.assertIn(EventKind.CAMERAS_ASSIGNED, kinds)
            self.assertIn(EventKind.CAPTURE_SUCCEEDED, kinds)
            capture_event = next(
                event for event in events if event.kind is EventKind.CAPTURE_SUCCEEDED
            )
            encoded = json.dumps(capture_event.details)
            self.assertIn("odd_page", encoded)
            self.assertTrue(Path(capture_event.details["even_preview"]).exists())

    def test_negative_pair_count_is_rejected_by_argument_parser(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--pairs", "-1"])
