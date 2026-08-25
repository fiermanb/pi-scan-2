"""Keyboard and GPIO-level input mapping independent of Kivy and Raspberry Pi."""

from pi_scan.commands import ApplicationCommand, CommandInProgress
from pi_scan.viewmodel import ScannerViewModel, ScannerViewState, UiScreen

# Pi Scan 1.5 was driven from a numeric keypad: 1 advanced, 3 and 5 ran the
# screen's secondary actions, 2 turned the cameras off, and 8/2/4/6 panned the
# preview wherever the screen did not claim those digits. That map is kept here
# so a keypad-only console remains fully operable, with the letter and arrow
# bindings layered on top for keyboards and touchscreens.
# A digit may name more than one command. 1.5 used "1" to continue from either
# error screen, where continuing meant dismissing a failed capture on one and
# re-preparing the cameras on the other. The first command the current state
# allows is the one that runs.
SCREEN_DIGITS: dict[UiScreen, dict[str, tuple[ApplicationCommand, ...]]] = {
    UiScreen.START: {"2": (ApplicationCommand.TURN_OFF_CAMERAS,)},
    UiScreen.COMPLETE: {"2": (ApplicationCommand.TURN_OFF_CAMERAS,)},
    UiScreen.PREPARATION: {
        "1": (ApplicationCommand.PREPARE,),
        # 1.5 offered the media update as "2" on the screen before the cameras.
        "2": (ApplicationCommand.APPLY_UPDATE,),
        "5": (ApplicationCommand.SWAP_CAMERAS,),
    },
    UiScreen.FOCUS_CONFIRMATION: {
        "1": (ApplicationCommand.FOCUS,),
        "3": (ApplicationCommand.FOCUS,),
        "5": (ApplicationCommand.SWAP_CAMERAS,),
    },
    # 1.5 never captured from a digit: capture was the pedal, B, C or space, so
    # that a mistaken keypress could not consume a page number.
    UiScreen.CAPTURE: {
        "3": (ApplicationCommand.RESCAN,),
        "5": (ApplicationCommand.FINISH,),
    },
    UiScreen.ERROR: {
        "1": (ApplicationCommand.DISMISS_FAILURE, ApplicationCommand.RECOVER),
        "2": (ApplicationCommand.SAVE_DEBUG_LOGS,),
        "3": (ApplicationCommand.SAVE_DEBUG_LOGS,),
    },
}

_PERMISSIONS: dict[ApplicationCommand, str] = {
    ApplicationCommand.PREPARE: "can_prepare",
    ApplicationCommand.FOCUS: "can_focus",
    ApplicationCommand.CAPTURE: "can_capture",
    ApplicationCommand.RESCAN: "can_rescan",
    ApplicationCommand.DISMISS_FAILURE: "can_dismiss_failure",
    ApplicationCommand.RECOVER: "can_recover",
    ApplicationCommand.SWAP_CAMERAS: "can_swap",
    ApplicationCommand.FINISH: "can_finish",
    ApplicationCommand.TURN_OFF_CAMERAS: "can_turn_off_cameras",
    ApplicationCommand.APPLY_UPDATE: "can_apply_update",
    ApplicationCommand.SAVE_DEBUG_LOGS: "can_save_debug_logs",
}

_PAN_KEYS: dict[str, tuple[float, float]] = {
    "left": (-0.1, 0.0),
    "a": (-0.1, 0.0),
    "4": (-0.1, 0.0),
    "right": (0.1, 0.0),
    "d": (0.1, 0.0),
    "6": (0.1, 0.0),
    "up": (0.0, 0.1),
    "w": (0.0, 0.1),
    "8": (0.0, 0.1),
    "down": (0.0, -0.1),
    "s": (0.0, -0.1),
    "2": (0.0, -0.1),
}


def is_allowed(state: ScannerViewState, command: ApplicationCommand) -> bool:
    permission = _PERMISSIONS.get(command)
    return permission is None or bool(getattr(state, permission))


class InputController:
    """Translate keyboard and active-low pedal input into safe UI actions."""

    def __init__(self) -> None:
        self._last_pedal_level: int | None = None

    def handle_key(self, key: str, view_model: ScannerViewModel) -> bool:
        normalized = key.lower()
        state = view_model.state

        claimed = SCREEN_DIGITS.get(state.screen, {})
        command = _first_allowed(state, claimed.get(normalized))
        if command is None:
            if normalized in {"+", "="}:
                view_model.zoom_preview(1.25)
                return True
            if normalized == "-":
                view_model.zoom_preview(0.8)
                return True
            if normalized == "0":
                view_model.reset_preview()
                return True
            pan = _PAN_KEYS.get(normalized)
            if pan is not None:
                view_model.pan_preview(*pan)
                return True

            if normalized in {" ", "spacebar", "space", "b", "c"}:
                command = ApplicationCommand.CAPTURE
            elif normalized == "r":
                command = ApplicationCommand.RESCAN
            elif normalized == "p":
                command = ApplicationCommand.PREPARE
            elif normalized == "f":
                command = ApplicationCommand.FOCUS
            elif normalized == "x":
                command = ApplicationCommand.RECOVER
            elif normalized == "tab":
                command = ApplicationCommand.SWAP_CAMERAS
            elif normalized in {"enter", "numpadenter"}:
                command = _default_command(state)

        if command is None or state.busy or not is_allowed(state, command):
            return False
        try:
            view_model.dispatch(command)
        except CommandInProgress:
            return False
        return True

    def handle_pedal_level(self, level: int, view_model: ScannerViewModel) -> bool:
        if level not in {0, 1}:
            raise ValueError("pedal level must be 0 or 1")
        previous = self._last_pedal_level
        self._last_pedal_level = level
        if previous != 1 or level != 0:
            return False
        state = view_model.state
        if state.busy or not state.can_capture:
            return False
        try:
            view_model.dispatch(ApplicationCommand.CAPTURE)
        except CommandInProgress:
            return False
        return True


def _first_allowed(
    state: ScannerViewState, candidates: tuple[ApplicationCommand, ...] | None
) -> ApplicationCommand | None:
    if candidates is None:
        return None
    for command in candidates:
        if is_allowed(state, command):
            return command
    return None


def _default_command(state: ScannerViewState) -> ApplicationCommand | None:
    if state.can_prepare:
        return ApplicationCommand.PREPARE
    if state.can_focus:
        return ApplicationCommand.FOCUS
    if state.can_capture:
        return ApplicationCommand.CAPTURE
    if state.can_dismiss_failure:
        return ApplicationCommand.DISMISS_FAILURE
    if state.can_recover:
        return ApplicationCommand.RECOVER
    return None
