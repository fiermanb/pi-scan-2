"""CHDK camera discovery and transport."""

from .camera import (
    ChdkCamera,
    ChdkCaptureSettings,
    calculate_zoom_step,
    capture_settings_from_legacy,
    parse_remote_integer,
)
from .discovery import discover, parse_device_list
from .errors import (
    ChdkCaptureError,
    ChdkCommandError,
    ChdkDiagnosticError,
    ChdkDiscoveryParseError,
    ChdkError,
    ChdkExecutableNotFound,
    ChdkLaunchError,
    ChdkTimeout,
)
from .models import ChdkConnection, ChdkDevice
from .transport import ChdkProcessResult, ChdkPtpTransport

__all__ = [
    "ChdkCommandError",
    "ChdkCamera",
    "ChdkCaptureError",
    "ChdkDiagnosticError",
    "ChdkCaptureSettings",
    "ChdkConnection",
    "ChdkDevice",
    "ChdkDiscoveryParseError",
    "ChdkError",
    "ChdkExecutableNotFound",
    "ChdkLaunchError",
    "ChdkProcessResult",
    "ChdkPtpTransport",
    "ChdkTimeout",
    "calculate_zoom_step",
    "capture_settings_from_legacy",
    "discover",
    "parse_remote_integer",
    "parse_device_list",
]
