from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_scan.cameras.fake import FakeCamera
from pi_scan.domain.capture import CapturePairError
from pi_scan.domain.configuration import (
    CameraConfiguration,
    CameraSide,
    ScannerConfiguration,
)
from pi_scan.domain.session import (
    CameraAssignmentError,
    InvalidSessionTransition,
    ScannerSession,
    SessionOperationError,
    SessionState,
    assign_camera_pair,
)


class SessionFakeCamera(FakeCamera):
    def __init__(self, identifier: str, **kwargs) -> None:
        super().__init__(identifier, **kwargs)
        self.probe_count = 0
        self.prepare_count = 0
        self.focus_count = 0
        self.prepare_failure = None

    def probe(self) -> None:
        self.probe_count += 1

    def prepare(self) -> None:
        self.prepare_count += 1
        if self.prepare_failure is not None:
            raise self.prepare_failure

    def autofocus_and_lock(self) -> None:
        self.focus_count += 1


class CameraAssignmentTests(TestCase):
    def test_uses_persisted_legacy_positions(self) -> None:
        first = SessionFakeCamera("first")
        second = SessionFakeCamera("second")
        configuration = ScannerConfiguration(
            {
                "first": CameraConfiguration(position=CameraSide.EVEN),
                "second": CameraConfiguration(position=CameraSide.ODD),
            }
        )
        pair = assign_camera_pair([first, second], configuration)
        self.assertIs(pair.even, first)
        self.assertIs(pair.odd, second)

    def test_assigns_missing_side_to_new_camera(self) -> None:
        known = SessionFakeCamera("known")
        new = SessionFakeCamera("new")
        configuration = ScannerConfiguration(
            {"known": CameraConfiguration(position=CameraSide.ODD)}
        )
        pair = assign_camera_pair([new, known], configuration)
        self.assertIs(pair.odd, known)
        self.assertIs(pair.even, new)

    def test_preserves_legacy_discovery_order_when_no_positions_exist(self) -> None:
        first = SessionFakeCamera("first")
        second = SessionFakeCamera("second")
        pair = assign_camera_pair([first, second], ScannerConfiguration({}))
        self.assertIs(pair.odd, first)
        self.assertIs(pair.even, second)

    def test_rejects_duplicate_position(self) -> None:
        cameras = [SessionFakeCamera("one"), SessionFakeCamera("two")]
        configuration = ScannerConfiguration(
            {
                "one": CameraConfiguration(position=CameraSide.ODD),
                "two": CameraConfiguration(position=CameraSide.ODD),
            }
        )
        with self.assertRaises(CameraAssignmentError):
            assign_camera_pair(cameras, configuration)


class ScannerSessionTests(TestCase):
    def test_finish_closes_session_against_more_captures(self) -> None:
        with TemporaryDirectory() as directory:
            session, _, _ = self.make_session(directory)
            session.prepare()
            session.focus()
            session.finish()
            self.assertEqual(session.state, SessionState.COMPLETE)
            with self.assertRaises(InvalidSessionTransition):
                session.capture()

    def make_session(self, directory: str):
        even = SessionFakeCamera("even", files={".jpg": b"even"})
        odd = SessionFakeCamera("odd", files={".jpg": b"odd"})
        pair = assign_camera_pair(
            [odd, even],
            ScannerConfiguration(
                {
                    "even": CameraConfiguration(position=CameraSide.EVEN),
                    "odd": CameraConfiguration(position=CameraSide.ODD),
                }
            ),
        )
        return ScannerSession(pair, Path(directory)), even, odd

    def test_full_prepare_focus_capture_workflow(self) -> None:
        with TemporaryDirectory() as directory:
            session, even, odd = self.make_session(directory)
            session.prepare()
            self.assertEqual(session.state, SessionState.READY_TO_FOCUS)
            session.focus()
            self.assertEqual(session.state, SessionState.READY)
            result = session.capture()
            self.assertEqual((result.even_page, result.odd_page), (0, 1))
            self.assertEqual(session.next_even_page, 2)
            self.assertEqual((even.prepare_count, odd.prepare_count), (1, 1))
            self.assertEqual((even.focus_count, odd.focus_count), (1, 1))

    def test_capture_failure_does_not_advance_pages_and_can_retry(self) -> None:
        with TemporaryDirectory() as directory:
            session, _, odd = self.make_session(directory)
            session.prepare()
            session.focus()
            odd.failure = RuntimeError("camera disconnected")
            with self.assertRaises(CapturePairError):
                session.capture()
            self.assertEqual(session.state, SessionState.CAPTURE_FAILED)
            self.assertEqual(session.next_even_page, 0)
            odd.failure = None
            session.capture()
            self.assertEqual(session.next_even_page, 2)

    def test_rescan_replaces_last_pair_without_advancing(self) -> None:
        with TemporaryDirectory() as directory:
            session, even, odd = self.make_session(directory)
            session.prepare()
            session.focus()
            session.capture()
            even.files = {".jpg": b"new-even"}
            odd.files = {".jpg": b"new-odd"}
            session.rescan()
            self.assertEqual(session.next_even_page, 2)
            self.assertEqual((Path(directory) / "0000.jpg").read_bytes(), b"new-even")

    def test_prepare_failure_records_side_and_failed_state(self) -> None:
        with TemporaryDirectory() as directory:
            session, _, odd = self.make_session(directory)
            odd.prepare_failure = RuntimeError("PTP failure")
            with self.assertRaises(SessionOperationError) as raised:
                session.prepare()
            self.assertIn(CameraSide.ODD, raised.exception.failures)
            self.assertEqual(session.state, SessionState.FAILED)

    def test_recovery_reprepares_and_refocuses_both_cameras(self) -> None:
        with TemporaryDirectory() as directory:
            session, even, odd = self.make_session(directory)
            odd.prepare_failure = RuntimeError("temporarily disconnected")
            with self.assertRaises(SessionOperationError):
                session.prepare()
            odd.prepare_failure = None
            session.recover()
            self.assertEqual(session.state, SessionState.READY)
            self.assertEqual((even.focus_count, odd.focus_count), (1, 1))

    def test_camera_pair_can_be_swapped_for_manual_correction(self) -> None:
        first = SessionFakeCamera("first")
        second = SessionFakeCamera("second")
        pair = assign_camera_pair([first, second], ScannerConfiguration({}))
        swapped = pair.swapped()
        self.assertIs(swapped.even, pair.odd)
        self.assertIs(swapped.odd, pair.even)

    def test_rejects_capture_before_focus(self) -> None:
        with TemporaryDirectory() as directory:
            session, _, _ = self.make_session(directory)
            with self.assertRaises(InvalidSessionTransition):
                session.capture()
