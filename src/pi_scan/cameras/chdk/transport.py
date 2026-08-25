"""Supervised subprocess boundary for the chdkptp command-line client."""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .errors import (
    ChdkCommandError,
    ChdkExecutableNotFound,
    ChdkLaunchError,
    ChdkProcessFailure,
    ChdkTimeout,
)
from .models import ChdkConnection


@dataclass(frozen=True, slots=True)
class ChdkProcessResult:
    command: tuple[str, ...]
    stdout: str
    stderr: str


class ChdkPtpTransport:
    """Run isolated chdkptp batches without shell interpolation."""

    def __init__(
        self,
        executable: Path | str = "chdkptp",
        *,
        timeout_seconds: float = 30.0,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> None:
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.executable = str(executable)
        self.timeout_seconds = timeout_seconds
        self.environment = None if environment is None else os.environ | dict(environment)
        self.working_directory = working_directory

    def run(
        self,
        commands: Sequence[str],
        *,
        connection: ChdkConnection | None = None,
    ) -> ChdkProcessResult:
        if not commands:
            raise ValueError("at least one chdkptp command is required")
        arguments = [self.executable, "-r"]
        if connection is not None:
            arguments.append(f"-c{connection.cli_spec()}")
        arguments.extend(f"-e{command}" for command in commands)

        try:
            completed = subprocess.run(  # noqa: S603 - fixed executable and argument vector
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.environment,
                cwd=self.working_directory,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise ChdkExecutableNotFound(
                f"chdkptp executable was not found: {self.executable}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ChdkTimeout(
                f"chdkptp exceeded its {self.timeout_seconds:g} second deadline"
            ) from error
        except OSError as error:
            raise ChdkLaunchError(f"chdkptp could not be started: {error}") from error

        command = tuple(arguments)
        if completed.returncode != 0:
            failure = ChdkProcessFailure(
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            raise ChdkCommandError(f"chdkptp exited with status {completed.returncode}", failure)
        return ChdkProcessResult(command, completed.stdout, completed.stderr)
