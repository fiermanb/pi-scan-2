"""Camera adapters used by Pi Scan."""

from .base import Camera, CameraIdentity
from .fake import FakeCamera, SimulatedCamera, SimulatedCameraError

__all__ = [
    "Camera",
    "CameraIdentity",
    "FakeCamera",
    "SimulatedCamera",
    "SimulatedCameraError",
]
