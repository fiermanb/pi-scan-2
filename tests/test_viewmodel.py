from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_scan.commands import ApplicationCommand, ApplicationCommandRunner
from pi_scan.domain.session import SessionOperationError
from pi_scan.events import ApplicationEvent, EventKind
from pi_scan.simulator import create_simulator
from pi_scan.viewmodel import PreviewViewport, ScannerViewModel, UiEventBridge, UiScreen


class UiEventBridgeTests(TestCase):
    def test_drains_events_in_order_and_honors_limit(self) -> None:
        bridge = UiEventBridge()
        bridge(ApplicationEvent(EventKind.DISCOVERY_STARTED, "one"))
        bridge(ApplicationEvent(EventKind.OPERATION_STARTED, "two"))
        self.assertEqual([event.message for event in bridge.drain(limit=1)], ["one"])
        self.assertEqual([event.message for event in bridge.drain()], ["two"])
        self.assertEqual(bridge.drain(), ())


class ScannerViewModelTests(TestCase):
    def test_hardware_warning_is_persistent_and_non_blocking(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            runner = ApplicationCommandRunner(create_simulator(Path(directory)))
            view_model = ScannerViewModel(runner, bridge)
            try:
                bridge(
                    ApplicationEvent(
                        EventKind.HARDWARE_WARNING,
                        "Foot pedal unavailable; use touch controls",
                    )
                )
                bridge(ApplicationEvent(EventKind.DISCOVERY_STARTED, "Finding cameras"))
                state = view_model.poll()
                self.assertEqual(state.warning, "Foot pedal unavailable; use touch controls")
                self.assertEqual(state.status, "Finding cameras")
                self.assertIsNone(state.error)
            finally:
                runner.shutdown()

    def test_storage_status_selects_storage_screen(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            runner = ApplicationCommandRunner(create_simulator(Path(directory)))
            view_model = ScannerViewModel(runner, bridge)
            try:
                bridge(
                    ApplicationEvent(
                        EventKind.STORAGE_STATUS,
                        "Insert one drive",
                        {"state": "no_volume", "volume_count": 0},
                    )
                )
                state = view_model.poll()
                self.assertEqual(state.screen, UiScreen.STORAGE)
                self.assertEqual(state.status, "Insert one drive")
            finally:
                runner.shutdown()

    def test_camera_setting_event_updates_the_assigned_side(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            runner = ApplicationCommandRunner(create_simulator(Path(directory)))
            view_model = ScannerViewModel(runner, bridge)
            try:
                bridge(
                    ApplicationEvent(
                        EventKind.CAMERAS_ASSIGNED,
                        "assigned",
                        {
                            "even_camera": "camera-a",
                            "odd_camera": "camera-b",
                            "even_backend": "chdk",
                            "odd_backend": "gphoto2",
                            "even_zoom": "5",
                            "odd_zoom": "5",
                            "even_shutter": "1/15",
                            "odd_shutter": "1/15",
                        },
                    )
                )
                bridge(
                    ApplicationEvent(
                        EventKind.CAMERA_SETTINGS_CHANGED,
                        "updated",
                        {"identifier": "camera-a", "zoom": "7.5", "shutter": "1/30"},
                    )
                )
                state = view_model.poll()
                self.assertEqual((state.even_zoom, state.even_shutter), ("7.5", "1/30"))
                self.assertEqual(state.odd_backend, "gphoto2")
            finally:
                runner.shutdown()

    def test_test_capture_event_updates_only_its_side_preview(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            runner = ApplicationCommandRunner(create_simulator(Path(directory)))
            view_model = ScannerViewModel(runner, bridge)
            try:
                bridge(
                    ApplicationEvent(
                        EventKind.TEST_CAPTURE_SUCCEEDED,
                        "test ready",
                        {"side": "odd", "preview": "/preview/test-odd.jpg"},
                    )
                )
                state = view_model.poll()
                self.assertEqual(state.odd_test_preview, "/preview/test-odd.jpg")
                self.assertIsNone(state.even_test_preview)
            finally:
                runner.shutdown()

    def test_complete_async_simulator_workflow_updates_controls(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            application = create_simulator(Path(directory), event_sink=bridge)
            runner = ApplicationCommandRunner(application)
            view_model = ScannerViewModel(runner, bridge)
            try:
                view_model.dispatch(ApplicationCommand.INITIALIZE).result(timeout=2)
                state = view_model.poll()
                self.assertEqual(state.scanner_state, "new")
                self.assertEqual(state.screen, UiScreen.PREPARATION)
                self.assertTrue(state.can_prepare)
                self.assertEqual(state.odd_camera, "sim-odd")

                view_model.dispatch(ApplicationCommand.PREPARE).result(timeout=2)
                state = view_model.poll()
                self.assertEqual(state.scanner_state, "ready_to_focus")
                self.assertEqual(state.screen, UiScreen.FOCUS_CONFIRMATION)
                self.assertTrue(state.can_focus)
                self.assertTrue(state.can_swap)

                view_model.dispatch(ApplicationCommand.FOCUS).result(timeout=2)
                state = view_model.poll()
                self.assertTrue(state.can_capture)
                self.assertEqual(state.screen, UiScreen.CAPTURE)

                view_model.dispatch(ApplicationCommand.CAPTURE).result(timeout=2)
                state = view_model.poll()
                self.assertEqual((state.last_even_page, state.last_odd_page), (0, 1))
                self.assertEqual(state.next_even_page, 2)
                self.assertTrue(Path(state.even_preview).exists())
                self.assertTrue(Path(state.odd_preview).exists())
                self.assertTrue(state.can_rescan)
                self.assertTrue(state.can_finish)
                view_model.zoom_preview(2)
                view_model.pan_preview(0.25, -0.25)
                self.assertEqual(view_model.state.viewport, PreviewViewport(2.0, 0.25, -0.25))
                view_model.dispatch(ApplicationCommand.FINISH).result(timeout=2)
                state = view_model.poll()
                self.assertEqual(state.screen, UiScreen.COMPLETE)
                self.assertFalse(state.can_capture)
            finally:
                runner.shutdown()

    def test_failure_event_exposes_recovery_controls(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            application = create_simulator(Path(directory), event_sink=bridge)
            runner = ApplicationCommandRunner(application)
            view_model = ScannerViewModel(runner, bridge)
            try:
                view_model.dispatch(ApplicationCommand.INITIALIZE).result(timeout=2)
                view_model.poll()
                application.session.cameras.odd.connected = False
                with self.assertRaises(SessionOperationError):
                    view_model.dispatch(ApplicationCommand.PREPARE).result(timeout=2)
                state = view_model.poll()
                self.assertEqual(state.scanner_state, "failed")
                self.assertEqual(state.screen, UiScreen.ERROR)
                self.assertIsNotNone(state.error)
                self.assertTrue(state.can_recover)
                self.assertFalse(state.can_save_debug_logs)
            finally:
                runner.shutdown()

    def test_chdk_failure_exposes_debug_log_control(self) -> None:
        with TemporaryDirectory() as directory:
            bridge = UiEventBridge()
            runner = ApplicationCommandRunner(create_simulator(Path(directory)))
            view_model = ScannerViewModel(runner, bridge)
            try:
                bridge(
                    ApplicationEvent(
                        EventKind.CAMERAS_ASSIGNED,
                        "assigned",
                        {
                            "even_camera": "canon",
                            "odd_camera": "nikon",
                            "even_backend": "chdk",
                            "odd_backend": "gphoto2",
                        },
                    )
                )
                bridge(
                    ApplicationEvent(
                        EventKind.STATE_CHANGED,
                        "failed",
                        {"state": "failed"},
                    )
                )
                self.assertTrue(view_model.poll().can_save_debug_logs)
            finally:
                runner.shutdown()
