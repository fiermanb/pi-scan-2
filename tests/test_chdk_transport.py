import subprocess
from unittest import TestCase
from unittest.mock import patch

from pi_scan.cameras.chdk.errors import (
    ChdkCommandError,
    ChdkExecutableNotFound,
    ChdkLaunchError,
    ChdkTimeout,
)
from pi_scan.cameras.chdk.models import ChdkConnection
from pi_scan.cameras.chdk.transport import ChdkPtpTransport


class ChdkTransportTests(TestCase):
    def test_rejects_invalid_timeout(self) -> None:
        for timeout in (0.0, -1.0, float("nan"), float("inf"), float("-inf")):
            with (
                self.subTest(timeout=timeout),
                self.assertRaisesRegex(ValueError, "finite and positive"),
            ):
                ChdkPtpTransport(timeout_seconds=timeout)

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_builds_documented_batch_arguments_without_a_shell(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "camera output", "")
        transport = ChdkPtpTransport("chdkptp", timeout_seconds=12)

        result = transport.run(
            ["=return get_buildinfo()"],
            connection=ChdkConnection("bus-0", "device-1"),
        )

        self.assertEqual(result.stdout, "camera output")
        run_mock.assert_called_once_with(
            [
                "chdkptp",
                "-r",
                "-c-b=bus%-0 -d=device%-1",
                "-e=return get_buildinfo()",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=None,
            cwd=None,
            timeout=12,
            check=False,
        )

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_environment_overrides_retain_system_environment(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        ChdkPtpTransport(environment={"LUA_PATH": "/opt/chdkptp/lua/?.lua"}).run(["list"])
        passed_environment = run_mock.call_args.kwargs["env"]
        self.assertEqual(passed_environment["LUA_PATH"], "/opt/chdkptp/lua/?.lua")
        self.assertIn("PATH", passed_environment)

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_preserves_process_diagnostics_on_failure(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 3, "partial", "USB error")
        transport = ChdkPtpTransport()

        with self.assertRaises(ChdkCommandError) as raised:
            transport.run(["list"])

        self.assertEqual(raised.exception.failure.returncode, 3)
        self.assertEqual(raised.exception.failure.stderr, "USB error")

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_converts_process_timeout(self, run_mock) -> None:
        run_mock.side_effect = subprocess.TimeoutExpired(["chdkptp"], 5)
        with self.assertRaises(ChdkTimeout):
            ChdkPtpTransport(timeout_seconds=5).run(["list"])

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_converts_missing_executable(self, run_mock) -> None:
        run_mock.side_effect = FileNotFoundError("missing")
        with self.assertRaises(ChdkExecutableNotFound):
            ChdkPtpTransport("missing-chdkptp").run(["list"])

    @patch("pi_scan.cameras.chdk.transport.subprocess.run")
    def test_converts_os_launch_failure(self, run_mock) -> None:
        run_mock.side_effect = PermissionError("not executable")
        with self.assertRaisesRegex(ChdkLaunchError, "not executable"):
            ChdkPtpTransport().run(["list"])

    def test_rejects_empty_batch(self) -> None:
        with self.assertRaises(ValueError):
            ChdkPtpTransport().run([])
