"""Discover gPhoto2 cameras and obtain stable serial identities."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from pi_scan.cameras.base import CameraIdentity

from .errors import GphotoError, GphotoParseError
from .transport import GphotoTransport

_CAMERA_LINE = re.compile(r"^(?P<model>.+?)\s{2,}(?P<port>usb:\d+,\d+)\s*$")
_CURRENT = re.compile(r"^Current:\s*(?P<value>.+?)\s*$", re.MULTILINE)
_SERIAL_KEY = "/main/status/serialnumber"


@dataclass(frozen=True, slots=True)
class GphotoDevice:
    model: str
    port: str
    serial: str

    @property
    def identity(self) -> CameraIdentity:
        return CameraIdentity(self.serial, self.model, "gphoto2")


def parse_detected_cameras(output: str) -> tuple[tuple[str, str], ...]:
    cameras: list[tuple[str, str]] = []
    unexpected: list[str] = []
    for original_line in output.splitlines():
        line = original_line.strip()
        if not line or line.startswith("Model ") or set(line) == {"-"}:
            continue
        match = _CAMERA_LINE.fullmatch(line)
        if match is not None:
            cameras.append((match.group("model").strip(), match.group("port")))
        else:
            unexpected.append(line)
    if unexpected:
        raise GphotoParseError(f"unexpected gphoto2 auto-detect output: {unexpected[0]}")
    return tuple(cameras)


def parse_current_value(output: str) -> str:
    match = _CURRENT.search(output)
    if match is None:
        raise GphotoParseError("gphoto2 config output contained no Current value")
    value = match.group("value").strip()
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GphotoParseError("gphoto2 config output contained an invalid Current value")
    return value


type DeviceErrorSink = Callable[[str, str, GphotoError], None]


def discover_cameras(
    transport: GphotoTransport,
    *,
    error_sink: DeviceErrorSink | None = None,
) -> tuple[GphotoDevice, ...]:
    detected = parse_detected_cameras(transport.run(["--auto-detect"]).stdout)
    devices: list[GphotoDevice] = []
    for model, port in detected:
        try:
            result = transport.run([f"--port={port}", f"--get-config={_SERIAL_KEY}"])
            serial = parse_current_value(result.stdout)
        except GphotoError as error:
            if error_sink is None:
                raise
            error_sink(model, port, error)
            continue
        devices.append(GphotoDevice(model, port, serial))
    return tuple(devices)
