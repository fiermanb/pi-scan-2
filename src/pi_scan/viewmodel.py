"""Thread-safe event bridge and framework-neutral scanner presentation state."""

from dataclasses import dataclass, replace
from enum import StrEnum
from queue import Empty, SimpleQueue

from pi_scan.commands import ApplicationCommand, ApplicationCommandRunner
from pi_scan.events import ApplicationEvent, EventKind, JsonValue


class UiEventBridge:
    """Move application events from worker threads to the UI thread."""

    def __init__(self) -> None:
        self._events: SimpleQueue[ApplicationEvent] = SimpleQueue()

    def __call__(self, event: ApplicationEvent) -> None:
        self._events.put(event)

    def drain(self, *, limit: int = 100) -> tuple[ApplicationEvent, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        events: list[ApplicationEvent] = []
        while len(events) < limit:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                break
        return tuple(events)


class UiScreen(StrEnum):
    START = "start"
    STORAGE = "storage"
    PREPARATION = "preparation"
    FOCUS_CONFIRMATION = "focus_confirmation"
    CAPTURE = "capture"
    ERROR = "error"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PreviewViewport:
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def zoomed(self, factor: float) -> "PreviewViewport":
        if factor <= 0:
            raise ValueError("zoom factor must be positive")
        return replace(self, scale=min(max(self.scale * factor, 0.5), 5.0))

    def panned(self, delta_x: float, delta_y: float) -> "PreviewViewport":
        return replace(
            self,
            offset_x=min(max(self.offset_x + delta_x, -1.0), 1.0),
            offset_y=min(max(self.offset_y + delta_y, -1.0), 1.0),
        )


@dataclass(frozen=True, slots=True)
class ScannerViewState:
    status: str = "Not initialized"
    scanner_state: str = "uninitialized"
    screen: UiScreen = UiScreen.START
    busy: bool = False
    even_camera: str | None = None
    odd_camera: str | None = None
    even_backend: str | None = None
    odd_backend: str | None = None
    even_zoom: str = "5"
    odd_zoom: str = "5"
    even_shutter: str = "1/15"
    odd_shutter: str = "1/15"
    even_test_preview: str | None = None
    odd_test_preview: str | None = None
    next_even_page: int = 0
    last_even_page: int | None = None
    last_odd_page: int | None = None
    even_preview: str | None = None
    odd_preview: str | None = None
    even_detail: str | None = None
    odd_detail: str | None = None
    viewport: PreviewViewport = PreviewViewport()
    error: str | None = None
    warning: str | None = None
    update_version: str | None = None
    can_prepare: bool = False
    can_focus: bool = False
    can_capture: bool = False
    can_rescan: bool = False
    can_dismiss_failure: bool = False
    can_recover: bool = False
    can_swap: bool = False
    can_finish: bool = False
    can_turn_off_cameras: bool = False
    can_apply_update: bool = False
    can_save_debug_logs: bool = False


class ScannerViewModel:
    """Convert domain events into controls and text consumable by any UI."""

    def __init__(self, runner: ApplicationCommandRunner, bridge: UiEventBridge) -> None:
        self.runner = runner
        self.bridge = bridge
        self.state = ScannerViewState()

    def dispatch(self, command: ApplicationCommand):
        future = self.runner.submit(command)
        self.state = replace(self.state, busy=True, error=None)
        return future

    def poll(self) -> ScannerViewState:
        for event in self.bridge.drain():
            self._apply(event)
        self.state = replace(self.state, busy=self.runner.busy)
        return self.state

    def configure_camera(
        self,
        identifier: str,
        *,
        zoom: str | None = None,
        shutter: str | None = None,
    ):
        future = self.runner.submit_action(
            "configure_camera",
            lambda: self.runner.application.configure_camera(
                identifier, zoom=zoom, shutter=shutter
            ),
        )
        self.state = replace(self.state, busy=True, error=None)
        return future

    def test_capture(self, identifier: str):
        future = self.runner.submit_action(
            "test_capture",
            lambda: self.runner.application.test_capture(identifier),
        )
        self.state = replace(self.state, busy=True, error=None)
        return future

    def request_detail(self, centre_x: float, centre_y: float):
        """Ask for an unscaled window of the last pair around the current viewport."""
        future = self.runner.submit_action(
            "inspect_detail",
            lambda: self.runner.application.inspect_detail(centre_x, centre_y),
        )
        self.state = replace(self.state, busy=True, error=None)
        return future

    def zoom_preview(self, factor: float) -> ScannerViewState:
        self.state = replace(self.state, viewport=self.state.viewport.zoomed(factor))
        return self.state

    def pan_preview(self, delta_x: float, delta_y: float) -> ScannerViewState:
        self.state = replace(self.state, viewport=self.state.viewport.panned(delta_x, delta_y))
        return self.state

    def reset_preview(self) -> ScannerViewState:
        self.state = replace(
            self.state,
            viewport=PreviewViewport(),
            even_detail=None,
            odd_detail=None,
        )
        return self.state

    def _apply(self, event: ApplicationEvent) -> None:
        state = self.state
        if event.kind is EventKind.HARDWARE_WARNING:
            state = replace(state, warning=event.message)
        elif event.kind is EventKind.STORAGE_STATUS:
            state = replace(
                state,
                screen=UiScreen.STORAGE,
                status=event.message,
                error=_optional_string(event.details.get("error")),
            )
        elif event.kind is EventKind.DISCOVERY_STARTED:
            state = replace(state, status=event.message, error=None)
        elif event.kind is EventKind.CAMERAS_ASSIGNED:
            state = replace(
                state,
                status=event.message,
                even_camera=_optional_string(event.details.get("even_camera")),
                odd_camera=_optional_string(event.details.get("odd_camera")),
                even_backend=_optional_string(event.details.get("even_backend")),
                odd_backend=_optional_string(event.details.get("odd_backend")),
                even_zoom=_optional_string(event.details.get("even_zoom")) or "5",
                odd_zoom=_optional_string(event.details.get("odd_zoom")) or "5",
                even_shutter=_optional_string(event.details.get("even_shutter")) or "1/15",
                odd_shutter=_optional_string(event.details.get("odd_shutter")) or "1/15",
                next_even_page=_optional_int(
                    event.details.get("next_even_page"), state.next_even_page
                ),
            )
        elif event.kind is EventKind.CAMERA_SETTINGS_CHANGED:
            identifier = _optional_string(event.details.get("identifier"))
            zoom = _optional_string(event.details.get("zoom"))
            shutter = _optional_string(event.details.get("shutter"))
            changes = {"status": event.message, "error": None}
            if identifier == state.even_camera:
                changes.update(even_zoom=zoom or state.even_zoom)
                changes.update(even_shutter=shutter or state.even_shutter)
            elif identifier == state.odd_camera:
                changes.update(odd_zoom=zoom or state.odd_zoom)
                changes.update(odd_shutter=shutter or state.odd_shutter)
            state = replace(state, **changes)
        elif event.kind is EventKind.TEST_CAPTURE_SUCCEEDED:
            side = _optional_string(event.details.get("side"))
            preview = _optional_string(event.details.get("preview"))
            if side == "even":
                state = replace(state, status=event.message, even_test_preview=preview)
            elif side == "odd":
                state = replace(state, status=event.message, odd_test_preview=preview)
        elif event.kind is EventKind.DEBUG_LOG_SAVED:
            state = replace(state, status=event.message)
        elif event.kind is EventKind.OPERATION_STARTED:
            state = replace(state, status=event.message, error=None)
        elif event.kind is EventKind.STATE_CHANGED:
            scanner_state = _optional_string(event.details.get("state")) or "unknown"
            state = replace(
                state,
                status=event.message,
                scanner_state=scanner_state,
                next_even_page=_optional_int(
                    event.details.get("next_even_page"), state.next_even_page
                ),
            )
            state = _with_controls(state)
        elif event.kind is EventKind.CAPTURE_SUCCEEDED:
            state = replace(
                state,
                status=event.message,
                last_even_page=_optional_int(event.details.get("even_page"), 0),
                last_odd_page=_optional_int(event.details.get("odd_page"), 1),
                even_preview=_optional_string(event.details.get("even_preview")),
                odd_preview=_optional_string(event.details.get("odd_preview")),
                even_detail=None,
                odd_detail=None,
            )
            state = _with_controls(state)
        elif event.kind is EventKind.UPDATE_AVAILABLE:
            state = replace(
                state,
                status=event.message,
                update_version=_optional_string(event.details.get("version")),
            )
        elif event.kind is EventKind.UPDATE_APPLIED:
            state = replace(state, status=event.message, update_version=None)
        elif event.kind is EventKind.DETAIL_READY:
            state = replace(
                state,
                even_detail=_optional_string(event.details.get("even_detail")),
                odd_detail=_optional_string(event.details.get("odd_detail")),
            )
        elif event.kind is EventKind.OPERATION_FAILED:
            state = replace(
                state,
                status=event.message,
                error=_optional_string(event.details.get("error")) or event.message,
            )
        self.state = state


def _with_controls(state: ScannerViewState) -> ScannerViewState:
    ready = state.scanner_state == "ready"
    screen = {
        "new": UiScreen.PREPARATION,
        "preparing": UiScreen.PREPARATION,
        "ready_to_focus": UiScreen.FOCUS_CONFIRMATION,
        "focusing": UiScreen.FOCUS_CONFIRMATION,
        "ready": UiScreen.CAPTURE,
        "capturing": UiScreen.CAPTURE,
        "capture_failed": UiScreen.ERROR,
        "failed": UiScreen.ERROR,
        "complete": UiScreen.COMPLETE,
    }.get(state.scanner_state, UiScreen.START)
    return replace(
        state,
        screen=screen,
        can_prepare=state.scanner_state == "new",
        can_focus=state.scanner_state == "ready_to_focus",
        # 1.5 kept capture off the failure screen so a pedal press could not
        # silently consume a page pair while an error was displayed.
        can_capture=ready,
        can_dismiss_failure=state.scanner_state == "capture_failed",
        can_rescan=ready and state.last_even_page is not None,
        can_recover=state.scanner_state in {"failed", "capture_failed"},
        can_swap=state.scanner_state in {"new", "ready_to_focus"},
        can_finish=state.scanner_state
        in {"new", "ready_to_focus", "ready", "capture_failed", "failed"},
        can_apply_update=state.update_version is not None and state.scanner_state == "new",
        can_turn_off_cameras=(
            state.scanner_state in {"new", "complete"}
            and (state.even_backend == "chdk" or state.odd_backend == "chdk")
        ),
        can_save_debug_logs=(
            state.scanner_state in {"failed", "capture_failed"}
            and (state.even_backend == "chdk" or state.odd_backend == "chdk")
        ),
    )


def _optional_string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: JsonValue, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
