"""Hardware-independent scanning rules."""

from .capture import CaptureCoordinator, CapturePairError, CapturePairResult
from .configuration import (
    CameraConfiguration,
    CameraSide,
    ConfigurationError,
    ScannerConfiguration,
    parse_legacy_configuration,
    save_legacy_configuration,
    serialize_legacy_configuration,
)
from .numbering import next_even_page, page_filename
from .session import (
    CameraAssignmentError,
    CameraPair,
    InvalidSessionTransition,
    ScannerSession,
    SessionOperationError,
    SessionState,
    assign_camera_pair,
)

__all__ = [
    "CaptureCoordinator",
    "CapturePairError",
    "CapturePairResult",
    "CameraConfiguration",
    "CameraSide",
    "CameraAssignmentError",
    "CameraPair",
    "ConfigurationError",
    "InvalidSessionTransition",
    "ScannerSession",
    "ScannerConfiguration",
    "SessionOperationError",
    "SessionState",
    "assign_camera_pair",
    "next_even_page",
    "page_filename",
    "parse_legacy_configuration",
    "save_legacy_configuration",
    "serialize_legacy_configuration",
]
