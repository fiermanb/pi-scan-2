from pathlib import Path

import pytest

from pi_scan.cameras.gphoto import (
    GphotoCamera,
    GphotoCaptureError,
    GphotoDevice,
    GphotoProcessResult,
)


class TransportStub:
    def __init__(self, on_run=None):
        self.commands = []
        self.on_run = on_run

    def run(self, arguments):
        self.commands.append(tuple(arguments))
        if self.on_run:
            self.on_run(arguments)
        return GphotoProcessResult((), "", "")


def test_probe_prepare_and_focus_preserve_manual_camera_settings():
    transport = TransportStub()
    camera = GphotoCamera(GphotoDevice("Nikon 1 J5", "usb:001,006", "serial"), transport)
    camera.prepare()
    camera.autofocus_and_lock()
    assert transport.commands == [
        ("--port=usb:001,006", "--summary"),
        ("--port=usb:001,006", "--summary"),
    ]


def test_capture_returns_jpeg_and_raw_sidecar(tmp_path):
    base = tmp_path / "0042"

    def create_files(arguments):
        base.with_suffix(".jpg").write_bytes(b"jpeg")
        base.with_suffix(".nef").write_bytes(b"raw")

    transport = TransportStub(create_files)
    camera = GphotoCamera(GphotoDevice("Nikon", "usb:1,2", "abc"), transport)
    assert camera.capture(base) == [base.with_suffix(".jpg"), base.with_suffix(".nef")]
    assert transport.commands[0] == (
        "--port=usb:1,2",
        "--capture-image-and-download",
        "--no-keep",
        "--force-overwrite",
        f"--filename={base}.%C",
    )


def test_capture_ignores_preexisting_files_and_requires_new_output(tmp_path):
    base = Path(tmp_path) / "0000"
    base.with_suffix(".jpg").write_bytes(b"old")
    camera = GphotoCamera(GphotoDevice("Nikon", "usb:1,2", "abc"), TransportStub())
    with pytest.raises(GphotoCaptureError):
        camera.capture(base)
