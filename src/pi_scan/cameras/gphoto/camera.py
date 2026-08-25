"""gPhoto2 camera implementation for DSLR and mirrorless cameras."""

from collections.abc import Sequence
from pathlib import Path

from pi_scan.cameras.base import CAPTURE_IMAGE_SUFFIXES, CameraIdentity

from .discovery import GphotoDevice
from .errors import GphotoCaptureError
from .transport import GphotoTransport


class GphotoCamera:
    def __init__(self, device: GphotoDevice, transport: GphotoTransport) -> None:
        self.device = device
        self.transport = transport

    @property
    def identity(self) -> CameraIdentity:
        return self.device.identity

    def probe(self) -> None:
        self.transport.run([self._port_argument, "--summary"])

    def prepare(self) -> None:
        # DSLR settings and optical zoom remain camera-controlled, as in Pi Scan 1.5.
        self.probe()

    def autofocus_and_lock(self) -> None:
        # Many gPhoto cameras cannot retain a remote focus lock. Capture performs AF.
        self.probe()

    def capture(self, destination_base: Path) -> Sequence[Path]:
        destination_base.parent.mkdir(parents=True, exist_ok=True)
        before = set(destination_base.parent.glob(destination_base.name + ".*"))
        template = str(destination_base) + ".%C"
        self.transport.run(
            [
                self._port_argument,
                "--capture-image-and-download",
                "--no-keep",
                "--force-overwrite",
                f"--filename={template}",
            ]
        )
        created = sorted(
            (
                path
                for path in destination_base.parent.glob(destination_base.name + ".*")
                if path not in before and path.suffix.lower() in CAPTURE_IMAGE_SUFFIXES
            ),
            key=lambda path: path.suffix.lower(),
        )
        if not created:
            raise GphotoCaptureError(
                "gphoto2 reported success but produced no supported image files"
            )
        return created

    @property
    def _port_argument(self) -> str:
        return f"--port={self.device.port}"
