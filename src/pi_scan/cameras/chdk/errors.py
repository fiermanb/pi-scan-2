"""Errors raised by the CHDKPTP adapter."""

from dataclasses import dataclass


class ChdkError(RuntimeError):
    """Base class for CHDK integration errors."""


class ChdkExecutableNotFound(ChdkError):
    """The configured chdkptp executable could not be started."""


class ChdkLaunchError(ChdkError):
    """The configured chdkptp process failed to start for an operating-system reason."""


class ChdkTimeout(ChdkError):
    """A chdkptp operation exceeded its deadline."""


@dataclass(frozen=True, slots=True)
class ChdkProcessFailure:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ChdkCommandError(ChdkError):
    """chdkptp exited unsuccessfully."""

    def __init__(self, message: str, failure: ChdkProcessFailure) -> None:
        super().__init__(message)
        self.failure = failure


class ChdkDiscoveryParseError(ChdkError):
    """Device-list output could not be interpreted safely."""


class ChdkCaptureError(ChdkError):
    """A CHDK capture completed without producing valid output."""


class ChdkDiagnosticError(ChdkError):
    """A CHDK diagnostic artifact could not be retrieved."""
