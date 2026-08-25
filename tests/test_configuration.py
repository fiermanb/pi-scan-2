from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pi_scan.domain.configuration import (
    CameraConfiguration,
    CameraSide,
    ConfigurationError,
    ScannerConfiguration,
    load_legacy_configuration,
    parse_legacy_configuration,
    save_legacy_configuration,
    serialize_legacy_configuration,
)


class LegacyConfigurationTests(TestCase):
    def test_migrates_per_serial_camera_settings(self) -> None:
        configuration = parse_legacy_configuration(
            """
            {
              "serial-even": {"position": "even", "zoom": "7.5", "shutter": "1/30"},
              "serial-odd": {"position": "odd"}
            }
            """
        )
        self.assertEqual(
            configuration.camera("serial-even"),
            CameraConfiguration(CameraSide.EVEN, "7.5", "1/30"),
        )
        self.assertEqual(
            configuration.camera("serial-odd"),
            CameraConfiguration(CameraSide.ODD, "5", "1/15"),
        )

    def test_unknown_camera_gets_legacy_defaults(self) -> None:
        configuration = parse_legacy_configuration("{}")
        self.assertEqual(configuration.camera("new-camera"), CameraConfiguration())

    def test_rejects_invalid_setting_instead_of_silently_defaulting(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "invalid zoom"):
            parse_legacy_configuration('{"camera": {"zoom": "far"}}')

    def test_rejects_non_object_top_level(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "top-level"):
            parse_legacy_configuration("[]")

    def test_rejects_malformed_legacy_configuration_shapes_and_values(self) -> None:
        cases = (
            ("{broken", "invalid JSON"),
            ('{"": {}}', "identifiers must be non-empty"),
            ('{"camera": []}', "must be an object"),
            ('{"camera": {"position": "middle"}}', "invalid position"),
            ('{"camera": {"zoom": 5}}', "invalid zoom"),
            ('{"camera": {"shutter": "automatic"}}', "invalid shutter"),
        )
        for encoded, message in cases:
            with self.subTest(encoded=encoded), self.assertRaisesRegex(ConfigurationError, message):
                parse_legacy_configuration(encoded)

    def test_rejects_duplicate_camera_identifier(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "duplicate JSON key: 'camera'"):
            parse_legacy_configuration('{"camera": {"zoom": "4"}, "camera": {"zoom": "8"}}')

    def test_rejects_duplicate_camera_setting(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "duplicate JSON key: 'position'"):
            parse_legacy_configuration('{"camera": {"position": "even", "position": "odd"}}')

    def test_serializes_legacy_compatible_json_deterministically(self) -> None:
        configuration = parse_legacy_configuration(
            '{"z": {"position": "odd"}, "a": {"position": "even", "zoom": "7"}}'
        )
        encoded = serialize_legacy_configuration(configuration)
        self.assertLess(encoded.index('"a"'), encoded.index('"z"'))
        self.assertEqual(parse_legacy_configuration(encoded), configuration)

    def test_atomic_save_round_trips_and_leaves_no_staging_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            configuration = ScannerConfiguration(
                {"camera": CameraConfiguration(CameraSide.ODD, "6", "1/20")}
            )
            save_legacy_configuration(path, configuration)
            self.assertEqual(load_legacy_configuration(path), configuration)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["pi-scan.conf"])

    def test_directory_sync_failure_restores_previous_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            original = ScannerConfiguration({"camera": CameraConfiguration(zoom="4")})
            updated = ScannerConfiguration({"camera": CameraConfiguration(zoom="8")})
            save_legacy_configuration(path, original)
            with (
                patch(
                    "pi_scan.domain.configuration._sync_directory",
                    side_effect=OSError("media sync failed"),
                ),
                self.assertRaisesRegex(OSError, "media sync failed"),
            ):
                save_legacy_configuration(path, updated)
            self.assertEqual(load_legacy_configuration(path), original)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["pi-scan.conf"])

    def test_first_save_sync_failure_removes_unconfirmed_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            with (
                patch(
                    "pi_scan.domain.configuration._sync_directory",
                    side_effect=OSError("media sync failed"),
                ),
                self.assertRaisesRegex(OSError, "media sync failed"),
            ):
                save_legacy_configuration(path, ScannerConfiguration({}))
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.iterdir()), [])

    def test_publish_failure_keeps_old_configuration_and_removes_backup(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            original = ScannerConfiguration({"camera": CameraConfiguration(zoom="4")})
            save_legacy_configuration(path, original)
            with (
                patch(
                    "pi_scan.domain.configuration.os.replace",
                    side_effect=OSError("rename failed"),
                ),
                self.assertRaisesRegex(OSError, "rename failed"),
            ):
                save_legacy_configuration(
                    path,
                    ScannerConfiguration({"camera": CameraConfiguration(zoom="8")}),
                )
            self.assertEqual(load_legacy_configuration(path), original)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["pi-scan.conf"])

    def test_backup_cleanup_sync_failure_does_not_undo_durable_update(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pi-scan.conf"
            original = ScannerConfiguration({"camera": CameraConfiguration(zoom="4")})
            updated = ScannerConfiguration({"camera": CameraConfiguration(zoom="8")})
            save_legacy_configuration(path, original)
            with patch(
                "pi_scan.domain.configuration._sync_directory",
                side_effect=(None, OSError("backup cleanup sync failed")),
            ):
                save_legacy_configuration(path, updated)
            self.assertEqual(load_legacy_configuration(path), updated)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["pi-scan.conf"])

    def test_updates_positions_without_losing_camera_settings(self) -> None:
        configuration = ScannerConfiguration(
            {
                "left": CameraConfiguration(zoom="8", shutter="1/30"),
                "right": CameraConfiguration(zoom="4", shutter="1/20"),
            }
        )
        updated = configuration.with_camera_positions(
            even_identifier="right", odd_identifier="left"
        )
        self.assertEqual(updated.camera("left"), CameraConfiguration(CameraSide.ODD, "8", "1/30"))
        self.assertEqual(updated.camera("right"), CameraConfiguration(CameraSide.EVEN, "4", "1/20"))

    def test_assigning_new_pair_clears_stale_roles_without_losing_settings(self) -> None:
        configuration = ScannerConfiguration(
            {
                "old-even": CameraConfiguration(CameraSide.EVEN, "8", "1/30"),
                "old-odd": CameraConfiguration(CameraSide.ODD, "4", "1/20"),
            }
        )
        updated = configuration.with_camera_positions(
            even_identifier="new-even", odd_identifier="new-odd"
        )
        self.assertEqual(updated.camera("old-even"), CameraConfiguration(None, "8", "1/30"))
        self.assertEqual(updated.camera("old-odd"), CameraConfiguration(None, "4", "1/20"))
        self.assertEqual(updated.camera("new-even").position, CameraSide.EVEN)
        self.assertEqual(updated.camera("new-odd").position, CameraSide.ODD)

    def test_updates_one_camera_setting_without_losing_role(self) -> None:
        configuration = ScannerConfiguration(
            {"left": CameraConfiguration(CameraSide.ODD, "5", "1/15")}
        )
        updated = configuration.with_camera_settings("left", zoom="7.5", shutter="1/30")
        self.assertEqual(
            updated.camera("left"),
            CameraConfiguration(CameraSide.ODD, "7.5", "1/30"),
        )

    def test_setting_update_uses_the_same_legacy_validation(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "invalid zoom"):
            ScannerConfiguration({}).with_camera_settings("camera", zoom="11")

    def test_configuration_updates_reject_invalid_identifiers_and_shutter(self) -> None:
        configuration = ScannerConfiguration({})
        cases = (
            (
                lambda: configuration.with_camera_positions(
                    even_identifier="",
                    odd_identifier="odd",
                ),
                "non-empty",
            ),
            (
                lambda: configuration.with_camera_positions(
                    even_identifier="same",
                    odd_identifier="same",
                ),
                "distinct",
            ),
            (lambda: configuration.with_camera_settings("", zoom="5"), "non-empty"),
            (
                lambda: configuration.with_camera_settings("camera", shutter="automatic"),
                "invalid shutter",
            ),
        )
        for update, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ConfigurationError, message):
                update()
