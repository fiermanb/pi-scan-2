from unittest import TestCase

from pi_scan.cameras.chdk.discovery import parse_device_list
from pi_scan.cameras.chdk.errors import ChdkDiscoveryParseError

DEVICE_LIST = r"""
___> list
1:Canon PowerShot D10 b=bus-0 d=\\.\libusb0-0001 v=0x4a9 p=0x31bc s=12345678
*2:Canon PowerShot A540 b=bus-0 d=\\.\libusb0-0002 v=0x4a9 p=0x311b s=87654321
"""


class ChdkDiscoveryTests(TestCase):
    def test_parses_documented_device_list_format(self) -> None:
        devices = parse_device_list(DEVICE_LIST)

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].model, "Canon PowerShot D10")
        self.assertEqual(devices[0].serial_number, "12345678")
        self.assertEqual(devices[0].identity.identifier, "12345678")
        self.assertEqual(devices[1].status, "*")
        self.assertEqual(devices[1].serial_number, "87654321")
        self.assertEqual(devices[1].identity.identifier, "87654321")
        self.assertEqual(devices[1].identity.backend, "chdk")

    def test_rejects_camera_without_a_serial_number(self) -> None:
        """Pi Scan 1.5 keyed pi-scan.conf by serial; a port-derived key would move."""
        output = r"1:Canon PowerShot A540 b=bus-0 d=device-1 v=0x4a9 p=0x311b s=nil"
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "reports no serial number"):
            parse_device_list(output)

    def test_connection_selector_escapes_lua_pattern_characters(self) -> None:
        device = parse_device_list(DEVICE_LIST)[0]
        self.assertEqual(
            device.connection.cli_spec(),
            r"-b=bus%-0 -d=\\%.\libusb0%-0001",
        )

    def test_rejects_reported_usb_query_error(self) -> None:
        output = "!1 b=bus-0 d=device-1 ERROR: failed to get device status"
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "failed to get device status"):
            parse_device_list(output)

    def test_rejects_unknown_output_instead_of_silently_losing_camera(self) -> None:
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "unexpected output"):
            parse_device_list("a format introduced by an incompatible chdkptp version")

    def test_rejects_duplicate_device_index(self) -> None:
        output = (
            "1:Canon One b=bus-0 d=device-1 v=0x4a9 p=0x1 s=one\n"
            "1:Canon Two b=bus-0 d=device-2 v=0x4a9 p=0x2 s=two"
        )
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "duplicate CHDK device index: 1"):
            parse_device_list(output)

    def test_rejects_duplicate_usb_connection(self) -> None:
        output = (
            "1:Canon One b=bus-0 d=device-1 v=0x4a9 p=0x1 s=one\n"
            "2:Canon Two b=bus-0 d=device-1 v=0x4a9 p=0x2 s=two"
        )
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "duplicate CHDK USB connection"):
            parse_device_list(output)

    def test_rejects_empty_camera_model(self) -> None:
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "model cannot be empty"):
            parse_device_list("1: b=bus-0 d=device-1 v=0x4a9 p=0x3259 s=serial")

    def test_rejects_invalid_usb_identifier(self) -> None:
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "invalid CHDK USB"):
            parse_device_list("1:Canon A2500 b=bus-0 d=device-1 v=not-a-vendor p=0x3259 s=serial")

    def test_appliance_mode_keeps_valid_devices_and_reports_bad_usb_entry(self) -> None:
        warnings = []
        devices = parse_device_list(
            DEVICE_LIST + "\n!3 b=bus-0 d=device-3 ERROR: failed to get device status",
            warning_sink=warnings.append,
        )
        self.assertEqual(len(devices), 2)
        self.assertEqual(
            warnings,
            [
                "bus-0/device-3: failed to get device status",
            ],
        )

    def test_appliance_mode_still_rejects_unknown_enumeration_format(self) -> None:
        with self.assertRaisesRegex(ChdkDiscoveryParseError, "unexpected output"):
            parse_device_list(
                DEVICE_LIST + "\nan unrelated diagnostic line",
                warning_sink=lambda warning: None,
            )
