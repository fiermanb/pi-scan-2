"""Kivy shell for either the simulator or a physical scanner appliance."""

import argparse
import math
import os
from collections.abc import Sequence
from concurrent.futures import Future
from pathlib import Path

from pi_scan import __version__
from pi_scan.appliance import HardwareApplication
from pi_scan.cameras.gphoto import GphotoTransport
from pi_scan.commands import (
    ApplicationCommand,
    ApplicationCommandRunner,
    CommandInProgress,
)
from pi_scan.domain.configuration import LEGACY_SHUTTER_SEQUENCE, LEGACY_ZOOM_SEQUENCE
from pi_scan.events import ApplicationEvent, EventKind
from pi_scan.hardware import GpioZeroPedal, Pedal, PedalUnavailable
from pi_scan.input import InputController
from pi_scan.simulator import create_simulator
from pi_scan.storage import LinuxRemovableStorage
from pi_scan.ui.input_profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    apply_input_profile,
    select_profile,
)
from pi_scan.viewmodel import ScannerViewModel, UiEventBridge, UiScreen


class KivyUnavailableError(RuntimeError):
    """The optional Kivy UI dependency is not installed."""


def _future_succeeded(future: Future[object]) -> bool:
    return not future.cancelled() and future.exception() is None


def _non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _non_negative_finite(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite, non-negative number")
    return parsed


def create_app(
    output: Path,
    *,
    hardware: bool = False,
    include_gphoto: bool = True,
    enable_gpio: bool = True,
    storage_timeout: float | None = None,
    minimum_free_bytes: int = 256 * 1024 * 1024,
    input_profile: str | None = None,
):
    """Create, but do not run, a simulator or physical Kivy application."""
    profile = select_profile(input_profile)
    try:
        # Input providers must be configured before Kivy creates its window.
        from kivy.config import Config

        apply_input_profile(profile, Config)

        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.transformation import Matrix
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.image import Image
        from kivy.uix.label import Label
        from kivy.uix.scatterlayout import ScatterLayout
        from kivy.uix.screenmanager import Screen, ScreenManager
    except ModuleNotFoundError as error:
        if error.name == "kivy" or (error.name and error.name.startswith("kivy.")):
            raise KivyUnavailableError(
                "Kivy is not installed; install Pi Scan with the 'ui' extra"
            ) from error
        raise

    bridge = UiEventBridge()
    hardware_application: HardwareApplication | None = None
    if hardware:
        storage = LinuxRemovableStorage()
        hardware_application = HardwareApplication(
            storage,
            gphoto_transport=GphotoTransport() if include_gphoto else None,
            event_sink=bridge,
            storage_timeout=storage_timeout,
            minimum_free_bytes=minimum_free_bytes,
        )
        application = hardware_application
    else:
        application = create_simulator(output, event_sink=bridge)
    runner = ApplicationCommandRunner(application)
    view_model = ScannerViewModel(runner, bridge)
    input_controller = InputController()

    class PiScanKivyApp(App):
        pedal: Pedal | None = None

        def build(self):
            root = BoxLayout(orientation="vertical", padding=16, spacing=8)
            mode = "appliance" if hardware else "simulator"
            self.status = Label(text=f"Pi Scan {mode}", size_hint_y=0.4)
            root.add_widget(self.status)
            self.buttons = {}
            self.setting_buttons = {}
            self.last_viewport = view_model.state.viewport
            self.detail_key = None
            self.manager = ScreenManager()
            self.manager.add_widget(self._make_screen(UiScreen.START, "Starting Pi Scan…", ()))
            self.manager.add_widget(
                self._make_screen(
                    UiScreen.STORAGE,
                    "Insert exactly one removable storage device.",
                    (),
                )
            )
            self.manager.add_widget(self._make_preparation_screen())
            self.manager.add_widget(
                self._make_screen(
                    UiScreen.FOCUS_CONFIRMATION,
                    "Place text pages against the platen, then focus and lock both cameras.",
                    (
                        ("Focus and lock", ApplicationCommand.FOCUS),
                        ("Swap cameras", ApplicationCommand.SWAP_CAMERAS),
                    ),
                )
            )
            self.manager.add_widget(self._make_capture_screen())
            self.manager.add_widget(
                self._make_screen(
                    UiScreen.COMPLETE,
                    "Scans are synchronized. Storage can now be removed safely.",
                    (("Turn off cameras", ApplicationCommand.TURN_OFF_CAMERAS),),
                )
            )
            self.manager.add_widget(
                self._make_screen(
                    UiScreen.ERROR,
                    "A camera operation failed. Reconnect cameras and recover.",
                    (
                        ("Continue", ApplicationCommand.DISMISS_FAILURE),
                        ("Save CHDK debug logs", ApplicationCommand.SAVE_DEBUG_LOGS),
                        ("Recover", ApplicationCommand.RECOVER),
                    ),
                )
            )
            root.add_widget(self.manager)
            Window.bind(on_key_down=self.on_key_down)
            Clock.schedule_interval(self.refresh, 0.05)
            Clock.schedule_once(lambda _delay: self.dispatch(ApplicationCommand.INITIALIZE), 0)
            if hardware and enable_gpio:
                try:
                    self.pedal = GpioZeroPedal(self._on_pedal_level)
                except PedalUnavailable as error:
                    self.pedal = None
                    bridge(
                        ApplicationEvent(
                            EventKind.HARDWARE_WARNING,
                            f"Foot pedal unavailable; use touch or keyboard controls: {error}",
                            {"component": "gpio_pedal", "error": str(error)},
                        )
                    )
            self.refresh(0)
            return root

        def _on_pedal_level(self, level):
            if input_controller.handle_pedal_level(level, view_model):
                Clock.schedule_once(self.refresh, 0)

        def _make_screen(self, screen, instructions, actions):
            widget = Screen(name=screen.value)
            layout = BoxLayout(orientation="vertical", spacing=8)
            layout.add_widget(Label(text=instructions))
            for label, command in actions:
                layout.add_widget(self._button(label, command))
            widget.add_widget(layout)
            return widget

        def _make_capture_screen(self):
            widget = Screen(name=UiScreen.CAPTURE.value)
            layout = BoxLayout(orientation="vertical", spacing=8)
            previews = BoxLayout(orientation="horizontal", spacing=8)
            self.even_scatter, self.even_preview = self._preview_pane(ScatterLayout, Image)
            self.odd_scatter, self.odd_preview = self._preview_pane(ScatterLayout, Image)
            previews.add_widget(self.even_scatter)
            previews.add_widget(self.odd_scatter)
            layout.add_widget(previews)
            layout.add_widget(self._button("Capture", ApplicationCommand.CAPTURE))
            layout.add_widget(self._button("Rescan", ApplicationCommand.RESCAN))
            done = Button(text="Done")
            done.bind(on_release=lambda _button: self.finish())
            layout.add_widget(done)
            self.buttons.setdefault(ApplicationCommand.FINISH, []).append(done)
            widget.add_widget(layout)
            return widget

        def _make_preparation_screen(self):
            widget = Screen(name=UiScreen.PREPARATION.value)
            layout = BoxLayout(orientation="vertical", spacing=8)
            layout.add_widget(Label(text="Configure CHDK cameras, then prepare both cameras."))
            previews = BoxLayout(orientation="horizontal", spacing=8)
            self.even_test_preview = Image(allow_stretch=True, keep_ratio=True)
            self.odd_test_preview = Image(allow_stretch=True, keep_ratio=True)
            previews.add_widget(self.even_test_preview)
            previews.add_widget(self.odd_test_preview)
            layout.add_widget(previews)
            for side in ("even", "odd"):
                row = BoxLayout(orientation="horizontal", spacing=8)
                for setting in ("zoom", "shutter"):
                    button = Button()
                    button.bind(
                        on_release=lambda _button, selected_side=side, selected_setting=setting: (
                            self.cycle_setting(selected_side, selected_setting)
                        )
                    )
                    self.setting_buttons[(side, setting)] = button
                    row.add_widget(button)
                test_button = Button(text=f"Test {side.title()}")
                test_button.bind(
                    on_release=lambda _button, selected_side=side: self.test_capture(selected_side)
                )
                self.setting_buttons[(side, "test")] = test_button
                row.add_widget(test_button)
                layout.add_widget(row)
            layout.add_widget(self._button("Prepare", ApplicationCommand.PREPARE))
            layout.add_widget(self._button("Swap cameras", ApplicationCommand.SWAP_CAMERAS))
            layout.add_widget(self._button("Turn off cameras", ApplicationCommand.TURN_OFF_CAMERAS))
            update = Button(text="Install update from media")
            update.bind(on_release=lambda _button: self.apply_update())
            layout.add_widget(update)
            self.buttons.setdefault(ApplicationCommand.APPLY_UPDATE, []).append(update)
            widget.add_widget(layout)
            return widget

        @staticmethod
        def _preview_pane(scatter_type, image_type):
            scatter = scatter_type(do_rotation=False, scale_min=0.5, scale_max=5.0)
            image = image_type(allow_stretch=True, keep_ratio=True)
            scatter.add_widget(image)
            return scatter, image

        def _button(self, label, command):
            button = Button(text=label)
            button.bind(on_release=lambda _button, selected=command: self.dispatch(selected))
            self.buttons.setdefault(command, []).append(button)
            return button

        def dispatch(self, command):
            try:
                view_model.dispatch(command)
            except CommandInProgress:
                return
            self.refresh(0)

        def finish(self):
            try:
                future = view_model.dispatch(ApplicationCommand.FINISH)
            except CommandInProgress:
                return
            future.add_done_callback(self._finish_completed)
            self.refresh(0)

        def _finish_completed(self, future):
            if _future_succeeded(future):
                Clock.schedule_once(lambda _delay: self.stop(), 0)
            else:
                Clock.schedule_once(self.refresh, 0)

        def apply_update(self):
            """Install an update from the media, then stop so the service restarts."""
            try:
                future = view_model.dispatch(ApplicationCommand.APPLY_UPDATE)
            except CommandInProgress:
                return
            future.add_done_callback(self._update_completed)
            self.refresh(0)

        def _update_completed(self, future):
            if _future_succeeded(future):
                Clock.schedule_once(lambda _delay: self.stop(), 0)
            else:
                Clock.schedule_once(self.refresh, 0)

        def cycle_setting(self, side, setting):
            state = view_model.state
            identifier = getattr(state, f"{side}_camera")
            backend = getattr(state, f"{side}_backend")
            if identifier is None or backend != "chdk" or state.busy:
                return
            sequence = LEGACY_ZOOM_SEQUENCE if setting == "zoom" else LEGACY_SHUTTER_SEQUENCE
            current = getattr(state, f"{side}_{setting}")
            selected = sequence[(sequence.index(current) + 1) % len(sequence)]
            try:
                view_model.configure_camera(
                    identifier,
                    **{setting: selected},
                )
            except CommandInProgress:
                return
            self.refresh(0)

        def test_capture(self, side):
            state = view_model.state
            identifier = getattr(state, f"{side}_camera")
            backend = getattr(state, f"{side}_backend")
            if identifier is None or backend != "chdk" or state.busy:
                return
            try:
                view_model.test_capture(identifier)
            except CommandInProgress:
                return
            self.refresh(0)

        def refresh(self, _delay):
            state = view_model.poll()
            self.manager.current = state.screen.value
            camera_text = f"Even: {state.even_camera or '-'}    Odd: {state.odd_camera or '-'}"
            page_text = f"Next pages: {state.next_even_page}, {state.next_even_page + 1}"
            error_text = f"\nError: {state.error}" if state.error else ""
            warning_text = f"\nWarning: {state.warning}" if state.warning else ""
            self.status.text = (
                f"{state.status}\n{camera_text}\n{page_text}{warning_text}{error_text}"
            )
            zoomed = state.viewport.scale > 1.0 and state.last_even_page is not None
            self._request_detail(state, zoomed)
            # Past 1:1 the fitted preview cannot answer whether the page is sharp,
            # so the unscaled crop replaces it and panning moves the crop window.
            showing_detail = zoomed and state.even_detail is not None
            sources = (
                (self.even_preview, state.even_detail if showing_detail else state.even_preview),
                (self.odd_preview, state.odd_detail if showing_detail else state.odd_preview),
            )
            for widget, source in sources:
                widget.allow_stretch = not showing_detail
                self._set_preview(widget, source)
            self._set_preview(self.even_test_preview, state.even_test_preview)
            self._set_preview(self.odd_test_preview, state.odd_test_preview)
            scale = 1.0 if showing_detail else state.viewport.scale
            self.even_scatter.scale = scale
            self.odd_scatter.scale = scale
            if not showing_detail:
                delta_x = state.viewport.offset_x - self.last_viewport.offset_x
                delta_y = state.viewport.offset_y - self.last_viewport.offset_y
                for scatter in (self.even_scatter, self.odd_scatter):
                    scatter.apply_transform(
                        Matrix().translate(delta_x * scatter.width, delta_y * scatter.height, 0)
                    )
            self.last_viewport = state.viewport
            controls = {
                ApplicationCommand.TURN_OFF_CAMERAS: state.can_turn_off_cameras,
                ApplicationCommand.APPLY_UPDATE: state.can_apply_update,
                ApplicationCommand.PREPARE: state.can_prepare,
                ApplicationCommand.SWAP_CAMERAS: state.can_swap,
                ApplicationCommand.FOCUS: state.can_focus,
                ApplicationCommand.CAPTURE: state.can_capture,
                ApplicationCommand.RESCAN: state.can_rescan,
                ApplicationCommand.DISMISS_FAILURE: state.can_dismiss_failure,
                ApplicationCommand.RECOVER: state.can_recover,
                ApplicationCommand.FINISH: state.can_finish,
                ApplicationCommand.SAVE_DEBUG_LOGS: state.can_save_debug_logs,
            }
            for command, enabled in controls.items():
                for button in self.buttons.get(command, ()):
                    button.disabled = state.busy or not enabled
            for (side, setting), button in self.setting_buttons.items():
                backend = getattr(state, f"{side}_backend")
                if setting != "test":
                    value = getattr(state, f"{side}_{setting}")
                    button.text = f"{side.title()} {setting.title()}: {value}"
                button.disabled = state.busy or not state.can_prepare or backend != "chdk"

        def _request_detail(self, state, zoomed):
            if not zoomed:
                self.detail_key = None
                return
            key = (round(state.viewport.offset_x, 2), round(state.viewport.offset_y, 2))
            if key == self.detail_key or state.busy:
                return
            try:
                view_model.request_detail(*key)
            except (CommandInProgress, RuntimeError):
                return
            self.detail_key = key

        def on_key_down(self, _window, keycode, _scancode, codepoint, _modifiers):
            key_names = {273: "up", 274: "down", 275: "right", 276: "left"}
            key = codepoint or key_names.get(keycode, "")
            handled = input_controller.handle_key(key, view_model)
            if handled:
                self.refresh(0)
            return handled

        @staticmethod
        def _set_preview(widget, source):
            if source and widget.source != source:
                widget.source = source
                widget.reload()

        def on_stop(self):
            if self.pedal is not None:
                self.pedal.close()
            if hardware_application is not None:
                hardware_application.cancel_startup()
            runner.shutdown(wait=True)
            if hardware_application is not None:
                hardware_application.close()

    return PiScanKivyApp()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-scan-ui")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--output", type=Path, default=Path("simulated-scans"))
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="use removable storage and physical CHDK/gPhoto2 cameras",
    )
    parser.add_argument(
        "--no-gphoto",
        action="store_true",
        help="in hardware mode, discover only mandatory CHDK cameras",
    )
    parser.add_argument(
        "--no-gpio",
        action="store_true",
        help="in hardware mode, disable the GPIO21 active-low pedal",
    )
    parser.add_argument(
        "--storage-timeout",
        type=_non_negative_finite,
        help="seconds to wait for exactly one removable volume (default: wait forever)",
    )
    parser.add_argument(
        "--input",
        choices=sorted(PROFILES),
        default=None,
        help=(
            "input hardware profile, as the two Pi Scan 1.5 images chose it "
            f"(default: {DEFAULT_PROFILE})"
        ),
    )
    parser.add_argument(
        "--minimum-free-mib",
        type=_non_negative,
        default=256,
        help="space reserved on scan media before capture is refused (default: 256)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    # 1.5 fixed the input hardware when the card was written. The environment
    # file is where that choice is now recorded; the flag overrides it.
    selected_input = arguments.input or os.environ.get("PI_SCAN_INPUT") or None
    try:
        app = create_app(
            arguments.output,
            hardware=arguments.hardware,
            include_gphoto=not arguments.no_gphoto,
            enable_gpio=not arguments.no_gpio,
            storage_timeout=arguments.storage_timeout,
            minimum_free_bytes=arguments.minimum_free_mib * 1024 * 1024,
            input_profile=selected_input,
        )
    except (KivyUnavailableError, ValueError, RuntimeError) as error:
        print(error)
        return 2
    app.run()
    return 0
