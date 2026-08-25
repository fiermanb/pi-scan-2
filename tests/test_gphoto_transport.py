import subprocess
from unittest.mock import patch

import pytest

from pi_scan.cameras.gphoto import (
    GphotoCommandError,
    GphotoExecutableNotFound,
    GphotoLaunchError,
    GphotoTimeout,
    GphotoTransport,
)


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        GphotoTransport(timeout_seconds=timeout)


def test_transport_uses_argument_vector_without_shell():
    completed = subprocess.CompletedProcess([], 0, "ok", "")
    with patch("pi_scan.cameras.gphoto.transport.subprocess.run", return_value=completed) as run:
        result = GphotoTransport(timeout_seconds=12).run(["--auto-detect"])
    assert result.stdout == "ok"
    assert run.call_args.args[0] == ("gphoto2", "--auto-detect")
    assert run.call_args.kwargs["timeout"] == 12
    assert run.call_args.kwargs["check"] is False


def test_transport_has_typed_process_failures():
    completed = subprocess.CompletedProcess([], 3, "", "camera busy")
    with (
        patch("pi_scan.cameras.gphoto.transport.subprocess.run", return_value=completed),
        pytest.raises(GphotoCommandError) as caught,
    ):
        GphotoTransport().run(["--summary"])
    assert caught.value.failure.returncode == 3
    assert caught.value.failure.stderr == "camera busy"


def test_transport_maps_missing_executable_and_timeout():
    with (
        patch("pi_scan.cameras.gphoto.transport.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(GphotoExecutableNotFound),
    ):
        GphotoTransport().run(["--auto-detect"])


def test_transport_maps_os_launch_failure():
    with (
        patch(
            "pi_scan.cameras.gphoto.transport.subprocess.run", side_effect=PermissionError("denied")
        ),
        pytest.raises(GphotoLaunchError, match="denied"),
    ):
        GphotoTransport().run(["--auto-detect"])
    with (
        patch(
            "pi_scan.cameras.gphoto.transport.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gphoto2", 1),
        ),
        pytest.raises(GphotoTimeout),
    ):
        GphotoTransport().run(["--auto-detect"])
