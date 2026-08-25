"""Framework-neutral events emitted by the Pi Scan application service."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def _empty_details() -> dict[str, JsonValue]:
    return {}


class EventKind(StrEnum):
    HARDWARE_WARNING = "hardware_warning"
    STORAGE_STATUS = "storage_status"
    DISCOVERY_STARTED = "discovery_started"
    CAMERAS_ASSIGNED = "cameras_assigned"
    CAMERA_SETTINGS_CHANGED = "camera_settings_changed"
    TEST_CAPTURE_SUCCEEDED = "test_capture_succeeded"
    DEBUG_LOG_SAVED = "debug_log_saved"
    OPERATION_STARTED = "operation_started"
    STATE_CHANGED = "state_changed"
    CAPTURE_SUCCEEDED = "capture_succeeded"
    DETAIL_READY = "detail_ready"
    UPDATE_AVAILABLE = "update_available"
    UPDATE_APPLIED = "update_applied"
    OPERATION_FAILED = "operation_failed"


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    kind: EventKind
    message: str
    details: dict[str, JsonValue] = field(default_factory=_empty_details)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
