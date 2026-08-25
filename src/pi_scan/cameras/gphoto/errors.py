"""Typed gPhoto2 failures."""

from dataclasses import dataclass


class GphotoError(RuntimeError):
    pass


class GphotoExecutableNotFound(GphotoError):
    pass


class GphotoLaunchError(GphotoError):
    pass


class GphotoTimeout(GphotoError):
    pass


class GphotoParseError(GphotoError):
    pass


class GphotoCaptureError(GphotoError):
    pass


@dataclass(frozen=True, slots=True)
class GphotoProcessFailure:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class GphotoCommandError(GphotoError):
    def __init__(self, message: str, failure: GphotoProcessFailure) -> None:
        self.failure = failure
        super().__init__(message)
