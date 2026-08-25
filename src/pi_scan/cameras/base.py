"""Interfaces shared by CHDK, gphoto2, and simulated cameras."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CAPTURE_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".nef", ".cr2", ".cr3", ".arw", ".dng", ".raf"}
)


@dataclass(frozen=True, slots=True)
class CameraIdentity:
    """Stable identity and display information for one camera."""

    identifier: str
    model: str
    backend: str


class Camera(Protocol):
    """Minimum camera contract needed by the capture coordinator."""

    @property
    def identity(self) -> CameraIdentity: ...

    def capture(self, destination_base: Path) -> Sequence[Path]: ...
