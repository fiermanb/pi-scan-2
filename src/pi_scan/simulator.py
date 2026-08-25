"""Command-line simulator exercising the complete scanner workflow."""

import argparse
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from pi_scan import __version__
from pi_scan.application import EventSink, PiScanApplication
from pi_scan.cameras.chdk import (
    ChdkCaptureSettings,
    ChdkDevice,
    ChdkPtpTransport,
)
from pi_scan.cameras.fake import SimulatedCamera
from pi_scan.domain.configuration import ScannerConfiguration, load_legacy_configuration
from pi_scan.events import ApplicationEvent


def _non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-scan-sim",
        description="Run the Pi Scan workflow with two simulated cameras.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulated-scans"),
        help="directory receiving numbered image files",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="optional legacy pi-scan.conf used for camera roles and settings",
    )
    parser.add_argument("--pairs", type=_non_negative, default=1)
    parser.add_argument("--dng", action="store_true", help="also create DNG sidecars")
    parser.add_argument("--quiet", action="store_true", help="do not print JSON events")
    return parser


def create_simulator(
    output: Path,
    *,
    configuration: ScannerConfiguration | None = None,
    include_dng: bool = False,
    event_sink: EventSink | None = None,
) -> PiScanApplication:
    configuration = configuration or ScannerConfiguration({})
    file_suffixes = {".jpg": _simulated_jpeg()}
    if include_dng:
        file_suffixes[".dng"] = b"simulated-dng"

    devices = (
        _device("sim-odd", 1),
        _device("sim-even", 2),
    )

    def discovery(transport: ChdkPtpTransport) -> Sequence[ChdkDevice]:
        del transport
        return devices

    def factory(
        device: ChdkDevice,
        transport: ChdkPtpTransport,
        settings: ChdkCaptureSettings,
    ) -> SimulatedCamera:
        del transport, settings
        return SimulatedCamera(device.identity.identifier, files=dict(file_suffixes))

    return PiScanApplication(
        ChdkPtpTransport("simulated-chdkptp"),
        output,
        configuration,
        event_sink=event_sink or (lambda event: None),
        device_discovery=discovery,
        camera_factory=factory,
    )


def event_as_json(event: ApplicationEvent) -> str:
    return json.dumps(
        {
            "timestamp": event.timestamp.isoformat(),
            "kind": event.kind.value,
            "message": event.message,
            "details": event.details,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    configuration = (
        load_legacy_configuration(arguments.config)
        if arguments.config is not None
        else ScannerConfiguration({})
    )

    def quiet_sink(event: ApplicationEvent) -> None:
        del event

    def printing_sink(event: ApplicationEvent) -> None:
        print(event_as_json(event))

    sink: EventSink = quiet_sink if arguments.quiet else printing_sink
    application = create_simulator(
        arguments.output,
        configuration=configuration,
        include_dng=arguments.dng,
        event_sink=sink,
    )
    try:
        application.initialize()
        application.prepare()
        application.focus()
        for _ in range(arguments.pairs):
            application.capture()
    except Exception as error:
        print(f"pi-scan-sim: {error}", file=sys.stderr)
        return 1
    return 0


def _device(identifier: str, index: int) -> ChdkDevice:
    return ChdkDevice(
        index=index,
        model=f"Simulated Canon {index}",
        bus="simulated-bus",
        device=f"simulated-device-{index}",
        vendor_id="0x4a9",
        product_id=f"0x{0x3200 + index:x}",
        serial_number=identifier,
        status="",
    )


def _simulated_jpeg() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (1200, 800), color=(238, 232, 210)).save(stream, format="JPEG")
    return stream.getvalue()
