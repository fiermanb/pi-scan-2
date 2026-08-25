from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pi_scan.cameras.fake import FakeCamera
from pi_scan.domain.capture import CaptureCoordinator, CapturePairError


class CaptureCoordinatorTests(TestCase):
    def test_commits_even_and_odd_files(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"even", ".dng": b"even-raw"}),
                FakeCamera("odd", files={".jpg": b"odd", ".dng": b"odd-raw"}),
            )
            result = coordinator.capture_pair(images, 12)
            self.assertEqual((result.even_page, result.odd_page), (12, 13))
            self.assertEqual((images / "0012.jpg").read_bytes(), b"even")
            self.assertEqual((images / "0013.jpg").read_bytes(), b"odd")
            self.assertEqual((images / "0012.dng").read_bytes(), b"even-raw")
            self.assertEqual((images / "0013.dng").read_bytes(), b"odd-raw")

    def test_camera_failure_does_not_publish_half_pair(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            coordinator = CaptureCoordinator(
                FakeCamera("even"), FakeCamera("odd", failure=RuntimeError("disconnected"))
            )
            with self.assertRaises(CapturePairError) as raised:
                coordinator.capture_pair(images, 0)
            self.assertIn("odd", raised.exception.failures)
            self.assertEqual(list(images.iterdir()), [])

    def test_rescan_replaces_existing_pair(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0000.jpg").write_bytes(b"old-even")
            (images / "0001.jpg").write_bytes(b"old-odd")
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"new-even"}),
                FakeCamera("odd", files={".jpg": b"new-odd"}),
            )
            coordinator.capture_pair(images, 0)
            self.assertEqual((images / "0000.jpg").read_bytes(), b"new-even")
            self.assertEqual((images / "0001.jpg").read_bytes(), b"new-odd")

    def test_camera_extension_case_is_normalized_when_replacing_legacy_pair(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0000.jpg").write_bytes(b"old-even")
            (images / "0001.jpg").write_bytes(b"old-odd")
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".JPG": b"new-even", ".DNG": b"raw-even"}),
                FakeCamera("odd", files={".JPEG": b"new-odd", ".CR3": b"raw-odd"}),
            )
            result = coordinator.capture_pair(images, 0)
            self.assertEqual(
                {path.name for path in result.even_files},
                {"0000.jpg", "0000.dng"},
            )
            self.assertEqual(
                {path.name for path in result.odd_files},
                {"0001.jpg", "0001.cr3"},
            )
            self.assertEqual((images / "0000.jpg").read_bytes(), b"new-even")
            names = {path.name for path in images.iterdir()}
            self.assertNotIn("0000.JPG", names)
            self.assertNotIn("0001.JPEG", names)

    def test_jpg_and_jpeg_from_one_camera_are_rejected_as_duplicate_types(self) -> None:
        with TemporaryDirectory() as directory:
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"one", ".JPEG": b"two"}),
                FakeCamera("odd"),
            )
            with self.assertRaisesRegex(CapturePairError, "duplicate file types"):
                coordinator.capture_pair(Path(directory), 0)

    def test_rejects_symbolic_link_camera_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            staging = Path(directory)
            artifact = staging / "even.jpg"
            artifact.write_bytes(b"not trusted through a link")
            with (
                patch.object(Path, "is_symlink", return_value=True),
                self.assertRaisesRegex(CapturePairError, "symbolic link"),
            ):
                CaptureCoordinator._validate_artifacts("even", staging / "even", [artifact])

    def test_rejects_unsupported_camera_artifact_type(self) -> None:
        with TemporaryDirectory() as directory:
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"image", ".txt": b"unexpected"}),
                FakeCamera("odd"),
            )
            with self.assertRaisesRegex(CapturePairError, "unsupported file type: .txt"):
                coordinator.capture_pair(Path(directory), 0)

    def test_raw_only_camera_result_does_not_publish_or_advance_pair(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".dng": b"raw-only"}),
                FakeCamera("odd"),
            )
            with self.assertRaisesRegex(CapturePairError, "even camera returned no JPEG"):
                coordinator.capture_pair(images, 0)
            self.assertEqual(list(images.iterdir()), [])

    def test_rejects_odd_start_page(self) -> None:
        coordinator = CaptureCoordinator(FakeCamera("even"), FakeCamera("odd"))
        with TemporaryDirectory() as directory, self.assertRaises(ValueError):
            coordinator.capture_pair(Path(directory), 3)

    def test_syncs_staged_files_before_publishing_directory_entries(self) -> None:
        with TemporaryDirectory() as directory:
            calls = []
            images = Path(directory)
            coordinator = CaptureCoordinator(
                FakeCamera("even"),
                FakeCamera("odd"),
                file_sync=lambda path: calls.append(("file", path.name)),
                directory_sync=lambda path: calls.append(("directory", path)),
            )
            coordinator.capture_pair(images, 0)
            self.assertEqual([kind for kind, _ in calls], ["file", "file", "directory"])
            self.assertEqual(calls[-1][1], images)

    def test_directory_sync_failure_restores_rescan_backups(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0000.jpg").write_bytes(b"old-even")
            (images / "0001.jpg").write_bytes(b"old-odd")

            def fail_sync(path):
                raise OSError("media sync failed")

            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"new-even"}),
                FakeCamera("odd", files={".jpg": b"new-odd"}),
                file_sync=lambda path: None,
                directory_sync=fail_sync,
            )
            with self.assertRaisesRegex(CapturePairError, "commit"):
                coordinator.capture_pair(images, 0)
            self.assertEqual((images / "0000.jpg").read_bytes(), b"old-even")
            self.assertEqual((images / "0001.jpg").read_bytes(), b"old-odd")

    def test_incomplete_rollback_preserves_old_files_for_manual_recovery(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0000.jpg").write_bytes(b"old-even")
            (images / "0001.jpg").write_bytes(b"old-odd")
            real_replace = __import__("os").replace

            def fail_backup_restore(source, target):
                if Path(source).parent.name == "backups":
                    raise OSError("media stopped accepting directory updates")
                real_replace(source, target)

            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"new-even"}),
                FakeCamera("odd", files={".jpg": b"new-odd"}),
                file_sync=lambda path: None,
                directory_sync=lambda path: (_ for _ in ()).throw(OSError("sync failed")),
            )
            with (
                patch("pi_scan.domain.capture.os.replace", side_effect=fail_backup_restore),
                self.assertRaisesRegex(CapturePairError, "rollback was incomplete") as raised,
            ):
                coordinator.capture_pair(images, 0)

            recovery = raised.exception.recovery_directory
            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertTrue(recovery.is_dir())
            self.assertEqual(
                {path.read_bytes() for path in (recovery / "backups").iterdir()},
                {b"old-even", b"old-odd"},
            )
            self.assertEqual(
                set(raised.exception.failures),
                {"restore:0000.jpg", "restore:0001.jpg"},
            )

    def test_rejects_case_variant_duplicate_file_types(self) -> None:
        with TemporaryDirectory() as directory:
            coordinator = CaptureCoordinator(
                FakeCamera("even", files={".jpg": b"one", ".JPG": b"two"}),
                FakeCamera("odd"),
            )
            with self.assertRaisesRegex(CapturePairError, "duplicate file types"):
                coordinator.capture_pair(Path(directory), 0)
