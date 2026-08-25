import builtins
from concurrent.futures import Future
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from pi_scan import __version__
from pi_scan.ui.kivy_app import (
    KivyUnavailableError,
    _future_succeeded,
    build_parser,
    create_app,
)


class KivyBoundaryTests(TestCase):
    def test_cli_reports_version_without_importing_kivy(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"pi-scan-ui {__version__}")

    def test_done_only_closes_ui_after_successful_finish(self) -> None:
        succeeded = Future()
        succeeded.set_result(None)
        failed = Future()
        failed.set_exception(RuntimeError("finish failed"))
        cancelled = Future()
        cancelled.cancel()
        self.assertTrue(_future_succeeded(succeeded))
        self.assertFalse(_future_succeeded(failed))
        self.assertFalse(_future_succeeded(cancelled))

    def test_module_import_does_not_require_kivy(self) -> None:
        import pi_scan.ui.kivy_app

        self.assertTrue(callable(pi_scan.ui.kivy_app.main))

    def test_create_app_reports_missing_optional_dependency(self) -> None:
        original_import = builtins.__import__

        def controlled_import(name, *args, **kwargs):
            if name == "kivy" or name.startswith("kivy."):
                raise ModuleNotFoundError("No module named 'kivy'", name="kivy")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=controlled_import),
            self.assertRaisesRegex(KivyUnavailableError, "ui.*extra"),
        ):
            create_app(Path("unused"))

    def test_hardware_command_line_switches_are_explicit(self) -> None:
        arguments = build_parser().parse_args(
            [
                "--hardware",
                "--no-gphoto",
                "--no-gpio",
                "--storage-timeout",
                "30",
                "--minimum-free-mib",
                "512",
            ]
        )
        self.assertTrue(arguments.hardware)
        self.assertTrue(arguments.no_gphoto)
        self.assertTrue(arguments.no_gpio)
        self.assertEqual(arguments.storage_timeout, 30)
        self.assertEqual(arguments.minimum_free_mib, 512)

    def test_negative_free_space_reserve_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--minimum-free-mib", "-1"])

    def test_invalid_storage_timeouts_are_rejected_before_ui_startup(self) -> None:
        for timeout in ("-1", "nan", "inf", "-inf"):
            with self.subTest(timeout=timeout), self.assertRaises(SystemExit):
                build_parser().parse_args(["--storage-timeout", timeout])

    def test_zero_storage_timeout_is_valid_for_single_poll_startup(self) -> None:
        arguments = build_parser().parse_args(["--storage-timeout", "0"])
        self.assertEqual(arguments.storage_timeout, 0.0)
