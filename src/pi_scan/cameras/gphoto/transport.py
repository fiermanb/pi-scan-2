"""Supervised subprocess boundary for gphoto2."""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .errors import (
    GphotoCommandError,
    GphotoExecutableNotFound,
    GphotoLaunchError,
    GphotoProcessFailure,
    GphotoTimeout,
)


@dataclass(frozen=True, slots=True)
class GphotoProcessResult:
    command: tuple[str, ...]
    stdout: str
    stderr: str


class GphotoTransport:
    def __init__(
        self,
        executable: Path | str = "gphoto2",
        *,
        timeout_seconds: float = 45.0,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.executable = str(executable)
        self.timeout_seconds = timeout_seconds
        self.environment = None if environment is None else os.environ | dict(environment)

    def run(self, arguments: Sequence[str]) -> GphotoProcessResult:
        command = (self.executable, *arguments)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed executable and argument vector
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.environment,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise GphotoExecutableNotFound(
                f"gphoto2 executable was not found: {self.executable}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise GphotoTimeout(
                f"gphoto2 exceeded its {self.timeout_seconds:g} second deadline"
            ) from error
        except OSError as error:
            raise GphotoLaunchError(f"gphoto2 could not be started: {error}") from error
        if completed.returncode:
            failure = GphotoProcessFailure(
                command, completed.returncode, completed.stdout, completed.stderr
            )
            raise GphotoCommandError(f"gphoto2 exited with status {completed.returncode}", failure)
        return GphotoProcessResult(command, completed.stdout, completed.stderr)
