import pytest

from pi_scan.cameras.gphoto import GphotoParseError, GphotoProcessResult
from pi_scan.cameras.gphoto.discovery import (
    discover_cameras,
    parse_current_value,
    parse_detected_cameras,
)


class TransportStub:
    def __init__(self):
        self.commands = []

    def run(self, arguments):
        self.commands.append(tuple(arguments))
        if arguments == ["--auto-detect"]:
            return GphotoProcessResult(
                (),
                """Model                          Port
----------------------------------------------------------
Nikon 1 J5                     usb:001,006
Canon EOS R                    usb:002,003
""",
                "",
            )
        serial = "NIKON-42" if "usb:001,006" in arguments[0] else "CANON-7"
        return GphotoProcessResult((), f"Label: Serial Number\nCurrent: {serial}\n", "")


def test_parse_detection_ignores_headers_and_returns_model_port_pairs():
    assert parse_detected_cameras("Model Port\n--------\nNikon 1 J5       usb:001,006\n") == (
        ("Nikon 1 J5", "usb:001,006"),
    )


def test_parse_detection_rejects_unknown_output_instead_of_silently_losing_camera():
    with pytest.raises(GphotoParseError, match="unexpected gphoto2 auto-detect output"):
        parse_detected_cameras("Model Port\n--------\nchanged output format\n")


def test_discovery_queries_stable_serial_for_each_camera():
    transport = TransportStub()
    cameras = discover_cameras(transport)
    assert [(item.model, item.port, item.serial) for item in cameras] == [
        ("Nikon 1 J5", "usb:001,006", "NIKON-42"),
        ("Canon EOS R", "usb:002,003", "CANON-7"),
    ]
    assert cameras[0].identity.backend == "gphoto2"
    assert len(transport.commands) == 3


def test_current_value_must_be_present():
    with pytest.raises(GphotoParseError):
        parse_current_value("Label: serial number")


@pytest.mark.parametrize("value", ["   ", "serial\tvalue", "serial\x7fvalue"])
def test_current_value_must_be_nonempty_and_control_free(value):
    with pytest.raises(GphotoParseError, match="invalid Current value|no Current value"):
        parse_current_value(f"Current: {value}\n")


def test_current_value_preserves_legitimate_internal_spaces():
    assert parse_current_value("Current: Camera Serial 42\n") == "Camera Serial 42"


def test_one_bad_serial_query_does_not_discard_other_detected_camera():
    class PartiallyFailingTransport(TransportStub):
        def run(self, arguments):
            if arguments != ["--auto-detect"] and "usb:001,006" in arguments[0]:
                return GphotoProcessResult((), "Label: Serial Number\n", "")
            return super().run(arguments)

    failures = []
    cameras = discover_cameras(
        PartiallyFailingTransport(),
        error_sink=lambda model, port, error: failures.append((model, port, error)),
    )
    assert [(camera.model, camera.serial) for camera in cameras] == [("Canon EOS R", "CANON-7")]
    assert [(model, port) for model, port, _error in failures] == [("Nikon 1 J5", "usb:001,006")]
    assert isinstance(failures[0][2], GphotoParseError)
