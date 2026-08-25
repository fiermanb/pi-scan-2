from dataclasses import replace
from unittest import TestCase

from pi_scan.commands import ApplicationCommand, CommandInProgress
from pi_scan.input import InputController
from pi_scan.viewmodel import PreviewViewport, ScannerViewState


class FakeViewModel:
    def __init__(self, state: ScannerViewState) -> None:
        self.state = state
        self.commands = []

    def dispatch(self, command):
        self.commands.append(command)

    def zoom_preview(self, factor):
        self.state = replace(self.state, viewport=self.state.viewport.zoomed(factor))

    def pan_preview(self, x, y):
        self.state = replace(self.state, viewport=self.state.viewport.panned(x, y))

    def reset_preview(self):
        self.state = replace(self.state, viewport=PreviewViewport())


class InputControllerTests(TestCase):
    def test_capture_keys_only_fire_when_capture_is_enabled(self) -> None:
        controller = InputController()
        disabled = FakeViewModel(ScannerViewState())
        self.assertFalse(controller.handle_key("space", disabled))
        enabled = FakeViewModel(ScannerViewState(can_capture=True))
        self.assertTrue(controller.handle_key("b", enabled))
        self.assertEqual(enabled.commands, [ApplicationCommand.CAPTURE])

    def test_enter_selects_current_workflow_default(self) -> None:
        controller = InputController()
        view_model = FakeViewModel(ScannerViewState(can_focus=True))
        self.assertTrue(controller.handle_key("enter", view_model))
        self.assertEqual(view_model.commands, [ApplicationCommand.FOCUS])

    def test_workflow_hotkeys_dispatch_only_their_enabled_command(self) -> None:
        cases = (
            ("r", "can_rescan", ApplicationCommand.RESCAN),
            ("p", "can_prepare", ApplicationCommand.PREPARE),
            ("f", "can_focus", ApplicationCommand.FOCUS),
            ("x", "can_recover", ApplicationCommand.RECOVER),
            ("tab", "can_swap", ApplicationCommand.SWAP_CAMERAS),
        )
        for key, capability, command in cases:
            with self.subTest(key=key):
                controller = InputController()
                view_model = FakeViewModel(replace(ScannerViewState(), **{capability: True}))
                self.assertTrue(controller.handle_key(key.upper(), view_model))
                self.assertEqual(view_model.commands, [command])

    def test_enter_follows_workflow_priority_and_supports_numpad(self) -> None:
        cases = (
            ("can_prepare", ApplicationCommand.PREPARE),
            ("can_focus", ApplicationCommand.FOCUS),
            ("can_capture", ApplicationCommand.CAPTURE),
            ("can_recover", ApplicationCommand.RECOVER),
        )
        for capability, command in cases:
            with self.subTest(capability=capability):
                view_model = FakeViewModel(replace(ScannerViewState(), **{capability: True}))
                self.assertTrue(InputController().handle_key("numpadenter", view_model))
                self.assertEqual(view_model.commands, [command])
        all_enabled = FakeViewModel(
            ScannerViewState(
                can_prepare=True,
                can_focus=True,
                can_capture=True,
                can_recover=True,
            )
        )
        self.assertTrue(InputController().handle_key("enter", all_enabled))
        self.assertEqual(all_enabled.commands, [ApplicationCommand.PREPARE])

    def test_keyboard_zoom_pan_and_reset_are_bounded(self) -> None:
        controller = InputController()
        view_model = FakeViewModel(ScannerViewState())
        for _ in range(20):
            controller.handle_key("+", view_model)
            controller.handle_key("right", view_model)
        self.assertEqual(view_model.state.viewport.scale, 5.0)
        self.assertEqual(view_model.state.viewport.offset_x, 1.0)
        controller.handle_key("0", view_model)
        self.assertEqual(view_model.state.viewport, PreviewViewport())

    def test_keyboard_zoom_out_and_all_pan_aliases(self) -> None:
        controller = InputController()
        view_model = FakeViewModel(ScannerViewState(viewport=PreviewViewport(scale=2.0)))
        for key in ("-", "left", "a", "up", "w", "down", "s"):
            self.assertTrue(controller.handle_key(key, view_model))
        self.assertAlmostEqual(view_model.state.viewport.scale, 1.6)
        self.assertAlmostEqual(view_model.state.viewport.offset_x, -0.2)
        self.assertAlmostEqual(view_model.state.viewport.offset_y, 0.0)

    def test_unknown_busy_and_dispatch_race_keys_are_not_consumed(self) -> None:
        class RacingViewModel(FakeViewModel):
            def dispatch(self, command):
                raise CommandInProgress("capture already started")

        controller = InputController()
        self.assertFalse(controller.handle_key("?", FakeViewModel(ScannerViewState())))
        self.assertFalse(controller.handle_key("enter", FakeViewModel(ScannerViewState())))
        self.assertFalse(
            controller.handle_key(
                "b",
                FakeViewModel(ScannerViewState(can_capture=True, busy=True)),
            )
        )
        self.assertFalse(
            controller.handle_key(
                "b",
                RacingViewModel(ScannerViewState(can_capture=True)),
            )
        )

    def test_active_low_pedal_triggers_once_per_press(self) -> None:
        controller = InputController()
        view_model = FakeViewModel(ScannerViewState(can_capture=True))
        self.assertFalse(controller.handle_pedal_level(1, view_model))
        self.assertTrue(controller.handle_pedal_level(0, view_model))
        self.assertFalse(controller.handle_pedal_level(0, view_model))
        self.assertEqual(view_model.commands, [ApplicationCommand.CAPTURE])
        controller.handle_pedal_level(1, view_model)
        self.assertTrue(controller.handle_pedal_level(0, view_model))
        self.assertEqual(len(view_model.commands), 2)

    def test_pedal_is_ignored_while_busy(self) -> None:
        controller = InputController()
        view_model = FakeViewModel(ScannerViewState(can_capture=True, busy=True))
        controller.handle_pedal_level(1, view_model)
        self.assertFalse(controller.handle_pedal_level(0, view_model))
        self.assertEqual(view_model.commands, [])

    def test_pedal_is_ignored_when_capture_disabled_or_dispatch_races(self) -> None:
        class RacingViewModel(FakeViewModel):
            def dispatch(self, command):
                raise CommandInProgress("capture already started")

        for view_model in (
            FakeViewModel(ScannerViewState(can_capture=False)),
            RacingViewModel(ScannerViewState(can_capture=True)),
        ):
            with self.subTest(view_model=type(view_model).__name__):
                controller = InputController()
                self.assertFalse(controller.handle_pedal_level(1, view_model))
                self.assertFalse(controller.handle_pedal_level(0, view_model))

    def test_invalid_pedal_level_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            InputController().handle_pedal_level(2, FakeViewModel(ScannerViewState()))
