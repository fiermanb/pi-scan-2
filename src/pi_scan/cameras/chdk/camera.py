"""CHDK camera adapter backed by documented chdkptp CLI commands."""

import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pi_scan.cameras.base import CameraIdentity
from pi_scan.domain.configuration import LEGACY_ZOOM_CHOICES, CameraConfiguration

from .errors import ChdkCaptureError, ChdkDiagnosticError
from .models import ChdkDevice
from .transport import ChdkPtpTransport

_SHUTTER_VALUE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?|[0-9]+/[0-9]+)$")
_REMOTE_INTEGER = re.compile(r"^[0-9]+:return:(?:number:)?(?P<value>-?[0-9]+)\s*$")


def quote_cli_argument(value: str) -> str:
    """Quote one chdkptp CLI argument without invoking an operating-system shell."""
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("chdkptp arguments cannot contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True, slots=True)
class ChdkCaptureSettings:
    shutter: str = "1/15"
    iso: int = 100
    download_dng: bool = False
    zoom: str = "5"

    def __post_init__(self) -> None:
        if _SHUTTER_VALUE.fullmatch(self.shutter) is None:
            raise ValueError("shutter must be a decimal or fraction such as 1/15")
        if not 1 <= self.iso <= 1_000_000:
            raise ValueError("iso must be between 1 and 1000000")
        zoom_factor(self.zoom)


def capture_settings_from_legacy(
    configuration: CameraConfiguration, *, download_dng: bool = False
) -> ChdkCaptureSettings:
    return ChdkCaptureSettings(
        shutter=configuration.shutter,
        zoom=configuration.zoom,
        download_dng=download_dng,
    )


def zoom_factor(choice: str) -> float:
    if choice not in LEGACY_ZOOM_CHOICES:
        raise ValueError(f"unsupported legacy zoom choice: {choice!r}")
    if choice == "Min Zoom":
        return 0.0
    if choice == "Max Zoom":
        return 1.0
    return float(choice) / 10.0


def round_half_away_from_zero(value: float) -> int:
    """Round as Python 2 did, so zoom steps match Pi Scan 1.5 exactly."""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def iso_to_sv96(iso: int) -> int:
    """Convert a market ISO to the APEX96 speed value, as chdkptp's util does."""
    if iso <= 0:
        raise ValueError("iso must be positive")
    return round_half_away_from_zero(96 * math.log2(iso / 3.125))


def calculate_zoom_step(step_count: int, choice: str) -> int:
    if step_count <= 0:
        raise ValueError("camera returned an invalid zoom-step count")
    return max(round_half_away_from_zero(step_count * zoom_factor(choice)) - 1, 0)


def parse_remote_integer(output: str) -> int:
    for line in output.splitlines():
        match = _REMOTE_INTEGER.fullmatch(line.strip())
        if match is not None:
            return int(match.group("value"))
    raise ChdkCaptureError(f"CHDK returned no integer result: {output!r}")


class ChdkCamera:
    """One CHDK camera selected by its discovered USB bus and device."""

    def __init__(
        self,
        device: ChdkDevice,
        transport: ChdkPtpTransport,
        *,
        settings: ChdkCaptureSettings | None = None,
    ) -> None:
        self.device = device
        self.transport = transport
        self.settings = settings or ChdkCaptureSettings()

    @property
    def identity(self) -> CameraIdentity:
        return self.device.identity

    def probe(self) -> None:
        """Verify that the selected camera accepts CHDK Lua commands."""
        self.transport.run(
            ["=return get_buildinfo()"],
            connection=self.device.connection,
        )

    def configure(self, settings: ChdkCaptureSettings) -> None:
        self.settings = settings

    def prepare(self) -> int:
        """Apply the legacy appliance's required CHDK shooting configuration."""
        query = self.transport.run(
            ["rec", "=return get_zoom_steps()"],
            connection=self.device.connection,
        )
        step = calculate_zoom_step(parse_remote_integer(query.stdout), self.settings.zoom)
        white_balance = 4 if _numeric_product_id(self.device.product_id) == 12970 else 3
        script = (
            "=enter_alt(); sleep(50); set_capture_mode(2); sleep(50); "
            "props=require('propcase'); set_prop(props.FLASH_MODE,2); "
            f"set_zoom({step}); sleep(250); set_nd_filter(2); "
            f"set_prop(props.WB_MODE,{white_balance}); "
            "set_prop(props.QUALITY,0); set_prop(props.RESOLUTION,0); return true"
        )
        self.transport.run([script], connection=self.device.connection)
        return step

    def set_focus_lock(self, locked: bool) -> None:
        value = 1 if locked else 0
        self.transport.run(
            [f"=set_aflock({value}); return true"],
            connection=self.device.connection,
        )

    def autofocus_and_lock(self) -> None:
        """Run autofocus against the platen and retain that focus for scanning."""
        self.transport.run(
            [
                "=set_aflock(0); sleep(50); press('shoot_half'); sleep(500); "
                "release('shoot_half'); sleep(50); set_aflock(1); sleep(50); return true"
            ],
            connection=self.device.connection,
        )

    def set_zoom(self, step: int) -> None:
        if step < 0:
            raise ValueError("zoom step cannot be negative")
        self.transport.run(
            [f"=set_zoom({step}); return get_zoom()"],
            connection=self.device.connection,
        )

    def capture(self, destination_base: Path) -> Sequence[Path]:
        destination_base.parent.mkdir(parents=True, exist_ok=True)
        before = set(destination_base.parent.glob(destination_base.name + ".*"))
        settings = self.settings
        options = [
            "-jpg",
            f"-tv={settings.shutter}",
            f"-svm={iso_to_sv96(settings.iso)}",
        ]
        if settings.download_dng:
            options.append("-dng")
        command = f"rs {quote_cli_argument(str(destination_base))} {' '.join(options)}"
        self.transport.run(["rec", command], connection=self.device.connection)

        created = sorted(
            (
                path
                for path in destination_base.parent.glob(destination_base.name + ".*")
                if path not in before and path.suffix.lower() in {".jpg", ".dng"}
            ),
            key=lambda path: path.suffix.lower(),
        )
        if not created:
            raise ChdkCaptureError("chdkptp reported success but produced no JPEG or DNG")
        return created

    def beep_failure(self) -> None:
        """Sound the camera's failure tone, as Pi Scan 1.5 did on every failure."""
        self.transport.run(
            ["=play_sound(6); return true"],
            connection=self.device.connection,
        )

    def power_off(self) -> None:
        """Press the camera's power button over PTP, as Pi Scan 1.5 did."""
        self.transport.run(
            ['=post_levent_to_ui("PressPowerButton"); return true'],
            connection=self.device.connection,
        )

    def download_romlog(self, destination: Path) -> Path:
        """Generate, download, and remove the camera's most recent Canon ROM log."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        generate = (
            '=call_event_proc("SystemEventInit"); call_event_proc("System.Create"); '
            'if os.stat("A/ROMLOG.LOG") then os.remove("A/ROMLOG.LOG") end; '
            'call_event_proc("GetLogToFile","A/ROMLOG.LOG",1); sleep(2000); return true'
        )
        cleanup = '=if os.stat("A/ROMLOG.LOG") then os.remove("A/ROMLOG.LOG") end; return true'
        operation_failed = False
        try:
            self.transport.run([generate], connection=self.device.connection)
            self.transport.run(
                [f"d ROMLOG.LOG {quote_cli_argument(str(temporary))}"],
                connection=self.device.connection,
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise ChdkDiagnosticError("chdkptp reported success but produced no camera ROM log")
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            return destination
        except BaseException:
            operation_failed = True
            raise
        finally:
            try:
                try:
                    self.transport.run([cleanup], connection=self.device.connection)
                except Exception:
                    if not operation_failed:
                        raise
            finally:
                temporary.unlink(missing_ok=True)


def _numeric_product_id(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise ChdkCaptureError(f"invalid camera product ID: {value!r}") from error
