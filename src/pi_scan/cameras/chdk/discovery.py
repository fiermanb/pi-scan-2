"""Parse and expose devices reported by the chdkptp list command."""

import re
from collections.abc import Callable

from .errors import ChdkDiscoveryParseError
from .models import ChdkDevice
from .transport import ChdkPtpTransport

_DEVICE_LINE = re.compile(
    r"^(?P<status>[*+!-]?)(?P<index>[0-9]+):(?P<model>.*?)\s+"
    r"b=(?P<bus>\S+)\s+d=(?P<device>\S+)\s+"
    r"v=(?P<vendor>\S+)\s+p=(?P<product>\S+)\s+s=(?P<serial>\S+)\s*$"
)
_ERROR_LINE = re.compile(
    r"^(?P<status>!)(?P<index>[0-9]+)\s+b=(?P<bus>\S+)\s+"
    r"d=(?P<device>\S+)\s+ERROR:\s*(?P<message>.+)$"
)
_USB_ID = re.compile(r"^(?:0x)?[0-9a-fA-F]+$")


type DiscoveryWarningSink = Callable[[str], None]


def parse_device_list(
    output: str,
    *,
    warning_sink: DiscoveryWarningSink | None = None,
) -> tuple[ChdkDevice, ...]:
    devices: list[ChdkDevice] = []
    errors: list[str] = []
    unexpected: list[str] = []
    seen_indices: set[int] = set()
    seen_connections: set[tuple[str, str]] = set()
    for original_line in output.splitlines():
        line = original_line.strip()
        if not line or line.endswith("> list"):
            continue
        match = _DEVICE_LINE.fullmatch(line)
        if match is not None:
            model = match.group("model").strip()
            vendor = match.group("vendor")
            product = match.group("product")
            if not model:
                raise ChdkDiscoveryParseError("CHDK device model cannot be empty")
            if _USB_ID.fullmatch(vendor) is None or _USB_ID.fullmatch(product) is None:
                raise ChdkDiscoveryParseError(
                    f"invalid CHDK USB vendor/product ID: {vendor}/{product}"
                )
            index = int(match.group("index"))
            connection = match.group("bus"), match.group("device")
            if index in seen_indices:
                raise ChdkDiscoveryParseError(f"duplicate CHDK device index: {index}")
            if connection in seen_connections:
                raise ChdkDiscoveryParseError(
                    f"duplicate CHDK USB connection: {connection[0]}/{connection[1]}"
                )
            seen_indices.add(index)
            seen_connections.add(connection)
            serial = match.group("serial")
            if serial == "nil":
                raise ChdkDiscoveryParseError(
                    f"CHDK camera {model!r} reports no serial number; "
                    "Pi Scan keys its configuration by serial"
                )
            devices.append(
                ChdkDevice(
                    index=index,
                    model=model,
                    bus=connection[0],
                    device=connection[1],
                    vendor_id=vendor,
                    product_id=product,
                    serial_number=serial,
                    status=match.group("status"),
                )
            )
            continue
        error_match = _ERROR_LINE.fullmatch(line)
        if error_match is not None:
            errors.append(
                f"{error_match.group('bus')}/{error_match.group('device')}: "
                f"{error_match.group('message')}"
            )
        else:
            unexpected.append(line)

    if unexpected:
        details = [f"unexpected output: {line}" for line in unexpected]
        raise ChdkDiscoveryParseError("; ".join(errors + details))
    if errors:
        if warning_sink is None:
            raise ChdkDiscoveryParseError("; ".join(errors))
        for detail in errors:
            warning_sink(detail)
    return tuple(devices)


def discover(
    transport: ChdkPtpTransport,
    *,
    warning_sink: DiscoveryWarningSink | None = None,
) -> tuple[ChdkDevice, ...]:
    """List CHDK cameras without connecting to an arbitrary first device."""
    return parse_device_list(
        transport.run(["list"]).stdout,
        warning_sink=warning_sink,
    )
