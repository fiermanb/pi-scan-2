import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pi_scan.appliance import (
    ApplianceStartupError,
    HardwareApplication,
    PhysicalCameraDiscovery,
    create_appliance,
)
from pi_scan.cameras.chdk import ChdkProcessResult
from pi_scan.cameras.gphoto import (
    GphotoExecutableNotFound,
    GphotoLaunchError,
    GphotoProcessResult,
    GphotoTimeout,
)
from pi_scan.storage import FakeRemovableStorage, StorageError, StorageVolume, StorageWaiter


class ChdkTransportStub:
    def run(self, commands, *, connection=None):
        if commands == ["list"]:
            return ChdkProcessResult(
                (),
                "1:Canon A2500 b=bus-0 d=device-1 v=0x4a9 p=0x3259 s=canon-1\n"
                "2:Canon A2500 b=bus-0 d=device-2 v=0x4a9 p=0x3259 s=canon-2\n",
                "",
            )
        return ChdkProcessResult((), "", "")


class NoChdkTransportStub:
    """No CHDK camera answers, which is when 1.5 fell back to gPhoto2."""

    def run(self, commands, *, connection=None):
        return ChdkProcessResult((), "", "")


class UnusedGphotoTransport:
    def run(self, arguments):
        raise AssertionError("gPhoto2 must not be queried when CHDK cameras answer")


class GphotoTransportStub:
    def run(self, arguments):
        if arguments == ["--auto-detect"]:
            return GphotoProcessResult((), "Model Port\n------\nNikon 1 J5       usb:001,006\n", "")
        return GphotoProcessResult((), "Current: nikon-1\n", "")


def mounted_volume(root):
    return StorageVolume(Path("/dev/fake1"), "exfat", mount_points=(root,), transport="usb")


def test_appliance_mounts_storage_and_builds_chdk_camera_pair(tmp_path):
    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    storage = MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    runtime = create_appliance(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=UnusedGphotoTransport(),
    )
    session = runtime.application.initialize()
    assert session.cameras.even.identity.backend == "chdk"
    assert session.cameras.odd.identity.backend == "chdk"
    assert runtime.application.image_directory.is_dir()
    records = [
        json.loads(line)
        for line in runtime.diagnostics_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["kind"] == "discovery_started"
    assert records[-1]["kind"] == "state_changed"
    runtime.eject()
    runtime.eject()
    assert storage.discover() == ()


@pytest.mark.parametrize("count", [0, 2])
def test_appliance_requires_exactly_one_removable_volume(count):
    volumes = [StorageVolume(Path(f"/dev/fake{index}"), "exfat") for index in range(count)]
    with pytest.raises(ApplianceStartupError, match=f"found {count}"):
        create_appliance(FakeRemovableStorage(volumes), chdk_transport=ChdkTransportStub())


def test_appliance_loads_camera_roles_from_mounted_configuration(tmp_path):
    (tmp_path / "pi-scan.conf").write_text(
        '{"canon-1": {"position": "odd"}, "canon-2": {"position": "even"}}',
        encoding="utf-8",
    )

    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    runtime = create_appliance(
        MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")]),
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=UnusedGphotoTransport(),
    )
    session = runtime.application.initialize()
    assert session.cameras.odd.identity.identifier == "canon-1"
    assert session.cameras.even.identity.identifier == "canon-2"


def test_gphoto_is_not_queried_when_chdk_cameras_answer():
    """Pi Scan 1.5 searched gPhoto2 only when no CHDK camera replied."""
    cameras = PhysicalCameraDiscovery(
        configuration=runtime_configuration(),
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=UnusedGphotoTransport(),
    ).discover()
    assert [(camera.identity.identifier, camera.identity.backend) for camera in cameras] == [
        ("canon-1", "chdk"),
        ("canon-2", "chdk"),
    ]


def test_gphoto_is_used_when_no_chdk_camera_answers():
    cameras = PhysicalCameraDiscovery(
        configuration=runtime_configuration(),
        chdk_transport=NoChdkTransportStub(),
        gphoto_transport=GphotoTransportStub(),
    ).discover()
    assert [(camera.identity.identifier, camera.identity.backend) for camera in cameras] == [
        ("nikon-1", "gphoto2"),
    ]


def test_duplicate_gphoto_serial_warns_instead_of_silently_omitting_camera():
    class DuplicateSerialTransport:
        def run(self, arguments):
            if arguments == ["--auto-detect"]:
                return GphotoProcessResult(
                    (),
                    "Model Port\n------\nNikon One       usb:001,006\n"
                    "Nikon Two       usb:001,007\n",
                    "",
                )
            return GphotoProcessResult((), "Current: duplicate-serial\n", "")

    events = []
    cameras = PhysicalCameraDiscovery(
        configuration=runtime_configuration(),
        chdk_transport=NoChdkTransportStub(),
        gphoto_transport=DuplicateSerialTransport(),
        event_sink=events.append,
    ).discover()
    assert [camera.identity.identifier for camera in cameras] == ["duplicate-serial"]
    assert len(events) == 1
    assert events[0].kind.value == "hardware_warning"
    assert events[0].details == {
        "component": "gphoto2",
        "identifier": "duplicate-serial",
        "port": "usb:001,007",
        "error": "duplicate stable camera identifier",
    }


@pytest.mark.parametrize(
    "failure",
    [
        GphotoExecutableNotFound("gphoto2 not installed"),
        GphotoLaunchError("gphoto2 is not executable"),
        GphotoTimeout("gphoto2 timed out"),
    ],
)
def test_optional_gphoto_failure_warns_without_stopping_startup(failure):
    class FailingGphotoTransport:
        def run(self, arguments):
            raise failure

    events = []
    cameras = PhysicalCameraDiscovery(
        configuration=runtime_configuration(),
        chdk_transport=NoChdkTransportStub(),
        gphoto_transport=FailingGphotoTransport(),
        event_sink=events.append,
    ).discover()
    assert cameras == ()
    assert events[0].kind.value == "hardware_warning"
    assert events[0].details["component"] == "gphoto2"
    assert events[0].details["error_type"] == type(failure).__name__


def runtime_configuration():
    from pi_scan.domain.configuration import ScannerConfiguration

    return ScannerConfiguration({})


def test_startup_failure_ejects_already_mounted_storage(tmp_path):
    (tmp_path / "pi-scan.conf").write_text("[]", encoding="utf-8")

    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    with pytest.raises(Exception, match="top-level"):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


def test_read_only_storage_is_rejected_and_ejected(tmp_path):
    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return StorageVolume(
                Path("/dev/fake1"),
                "exfat",
                mount_points=(tmp_path,),
                read_only=True,
            )

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    with pytest.raises(Exception, match="read-only"):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


@pytest.mark.parametrize(
    ("mount_points", "message"),
    [
        ((), "exactly one mount point"),
        ((Path("relative-mount"),), "not absolute"),
    ],
)
def test_invalid_mount_point_is_rejected_and_ejected(mount_points, message):
    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return StorageVolume(Path("/dev/fake1"), "exfat", mount_points=mount_points)

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    with pytest.raises(Exception, match=message):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


def test_multiply_mounted_storage_is_rejected_and_ejected(tmp_path):
    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return StorageVolume(
                Path("/dev/fake1"),
                "exfat",
                mount_points=(tmp_path, tmp_path / "second"),
            )

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    with pytest.raises(Exception, match="found 2"):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


@pytest.mark.parametrize(
    "managed_name",
    ["images", "pi-scan.conf", "debug", "events.jsonl", ".pi-scan-previews"],
)
def test_symbolic_link_managed_path_is_rejected_and_ejected(tmp_path, managed_name):
    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    real_is_symlink = Path.is_symlink

    def selected_path_is_symlink(path):
        return path.name == managed_name or real_is_symlink(path)

    with (
        patch.object(Path, "is_symlink", selected_path_is_symlink),
        pytest.raises(StorageError, match="symbolic link"),
    ):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


@pytest.mark.parametrize("invalid_path", ["images", "debug/events.jsonl"])
def test_wrong_managed_path_type_is_rejected_and_ejected(tmp_path, invalid_path):
    path = tmp_path / invalid_path
    if path.name == "images":
        path.write_bytes(b"not a directory")
    else:
        path.mkdir(parents=True)

    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    with pytest.raises(StorageError, match="not a directory|not a regular file"):
        create_appliance(storage, chdk_transport=ChdkTransportStub())
    assert storage.ejected


def test_unwritable_storage_is_rejected_and_ejected_before_camera_discovery(tmp_path):
    class TrackingStorage(FakeRemovableStorage):
        ejected = False

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            self.ejected = True

    storage = TrackingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])

    def reject_write(_root):
        raise OSError("permission denied")

    with pytest.raises(OSError, match="permission denied"):
        create_appliance(
            storage,
            chdk_transport=ChdkTransportStub(),
            write_probe=reject_write,
        )
    assert storage.ejected


def test_hardware_application_reports_storage_then_initializes(tmp_path):
    events = []

    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    storage = MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=GphotoTransportStub(),
        event_sink=events.append,
        minimum_free_bytes=0,
    )
    session = hardware.initialize()
    assert session.state.value == "new"
    assert events[0].kind.value == "storage_status"
    assert events[0].details["state"] == "ready"
    hardware.close()


def test_hardware_finish_retries_a_failing_ejection_before_the_deadline(tmp_path):
    """Pi Scan 1.5 kept retrying the unmount while the media finished syncing."""

    class RetryableEjectStorage(FakeRemovableStorage):
        eject_count = 0

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            self.eject_count += 1
            if self.eject_count == 1:
                raise StorageError("target is busy")
            return super().eject(volume)

    storage = RetryableEjectStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    elapsed = [0.0]
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
        clock=lambda: elapsed[0],
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    session = hardware.initialize()
    hardware.finish()
    assert session.state.value == "complete"
    assert storage.eject_count == 2
    hardware.close()


def test_hardware_finish_forces_the_unmount_once_the_grace_period_expires(tmp_path):
    class StubbornStorage(FakeRemovableStorage):
        forced = False

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            if not self.forced:
                raise StorageError("target is busy")
            return super().eject(volume)

        def unmount(self, volume, *, force=False):
            if force:
                self.forced = True
            return super().unmount(volume, force=force)

    storage = StubbornStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    elapsed = [0.0]
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
        force_eject_after=5.0,
        clock=lambda: elapsed[0],
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    session = hardware.initialize()
    hardware.finish()
    assert storage.forced is True
    assert session.state.value == "complete"
    hardware.close()


def test_hardware_finish_powers_off_after_an_unmount_that_already_succeeded(tmp_path):
    class PartlyEjectedStorage(FakeRemovableStorage):
        """udisksctl unmounted the filesystem and then failed to power the drive off."""

        def __init__(self, volumes):
            super().__init__(volumes)
            self.filesystem_mounted = True
            self.power_off_attempts = 0

        def mount(self, volume):
            return mounted_volume(tmp_path)

        def unmount(self, volume, *, force=False):
            if not self.filesystem_mounted:
                raise StorageError("Error unmounting /dev/fake1: Not mounted")
            self.filesystem_mounted = False

        def eject(self, volume):
            if self.filesystem_mounted:
                self.unmount(volume)
            self.power_off_attempts += 1
            if self.power_off_attempts <= 2:
                raise StorageError("target is busy")
            return super().eject(volume)

    storage = PartlyEjectedStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    elapsed = [0.0]
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
        force_eject_after=2.0,
        clock=lambda: elapsed[0],
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    session = hardware.initialize()
    hardware.finish()
    assert storage.power_off_attempts == 3
    assert session.state.value == "complete"
    hardware.close()


def test_hardware_finish_reports_the_power_off_failure_not_the_stale_unmount(tmp_path):
    class AlreadyUnmountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

        def unmount(self, volume, *, force=False):
            raise StorageError("Error unmounting /dev/fake1: Not mounted")

        def eject(self, volume):
            raise StorageError("power-off failed")

    storage = AlreadyUnmountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    elapsed = [0.0]
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
        force_eject_after=2.0,
        clock=lambda: elapsed[0],
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    session = hardware.initialize()
    with pytest.raises(StorageError, match="power-off failed"):
        hardware.finish()
    assert session.state.value == "new"
    hardware.close()


def test_hardware_finish_leaves_session_retryable_when_even_forcing_fails(tmp_path):
    class UnejectableStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

        def eject(self, volume):
            raise StorageError("power-off failed")

    storage = UnejectableStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    elapsed = [0.0]
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
        force_eject_after=3.0,
        clock=lambda: elapsed[0],
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    session = hardware.initialize()
    with pytest.raises(StorageError, match="including a forced unmount"):
        hardware.finish()
    assert session.state.value == "new"
    hardware.close()


def test_hardware_application_rejects_reinitialization_without_remounting(tmp_path):
    class CountingStorage(FakeRemovableStorage):
        mount_count = 0

        def mount(self, volume):
            self.mount_count += 1
            return mounted_volume(tmp_path)

    storage = CountingStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=GphotoTransportStub(),
        minimum_free_bytes=0,
    )
    hardware.initialize()
    with pytest.raises(ApplianceStartupError, match="already initialized"):
        hardware.initialize()
    assert storage.mount_count == 1
    hardware.close()


def test_hardware_application_close_is_terminal_before_initialization():
    hardware = HardwareApplication(
        FakeRemovableStorage(),
        chdk_transport=ChdkTransportStub(),
    )
    hardware.close()
    hardware.close()
    with pytest.raises(ApplianceStartupError, match="closed"):
        hardware.initialize()


def test_hardware_application_rejects_operations_after_close(tmp_path):
    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    hardware = HardwareApplication(
        MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")]),
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=GphotoTransportStub(),
        minimum_free_bytes=0,
    )
    hardware.initialize()
    hardware.close()
    with pytest.raises(ApplianceStartupError, match="closed"):
        hardware.prepare()


def test_hardware_application_startup_can_be_cancelled():
    storage = FakeRemovableStorage()
    waiter = None

    def cancel(_seconds):
        assert waiter is not None
        waiter.cancel()

    waiter = StorageWaiter(storage, sleeper=cancel)
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        waiter=waiter,
    )
    with pytest.raises(Exception, match="cancelled"):
        hardware.initialize()


def test_human_readable_error_log_is_written_beside_the_json_events(tmp_path):
    """Field staff read debug/error.log off the media, as they did with 1.5."""

    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    runtime = create_appliance(
        MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")]),
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
    )
    runtime.application.initialize()
    lines = (tmp_path / "debug" / "error.log").read_text(encoding="utf-8").splitlines()
    assert lines
    assert all(" -- " in line for line in lines)
    assert any("Searching for cameras" in line for line in lines)


def test_update_archive_on_the_media_is_announced_and_installed(tmp_path):
    import zipfile

    archive = tmp_path / "pi-scan-update-9.9.archive"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("pi_scan-9.9.0-py3-none-any.whl", "wheel")

    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    events = []
    storage = MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")])
    hardware = HardwareApplication(
        storage,
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        event_sink=events.append,
        minimum_free_bytes=0,
    )
    hardware.initialize()
    announcements = [event for event in events if event.kind.value == "update_available"]
    assert [event.details["version"] for event in announcements] == ["9.9"]

    with patch("pi_scan.appliance.apply_update", return_value="pi_scan-9.9.0-py3-none-any.whl"):
        assert hardware.apply_update() == "9.9"
    assert storage.discover() == ()
    assert [event.kind.value for event in events][-1] == "update_applied"


def test_no_update_is_offered_when_the_media_carries_none(tmp_path):
    class MountedStorage(FakeRemovableStorage):
        def mount(self, volume):
            return mounted_volume(tmp_path)

    hardware = HardwareApplication(
        MountedStorage([StorageVolume(Path("/dev/fake1"), "exfat")]),
        chdk_transport=ChdkTransportStub(),
        gphoto_transport=None,
        minimum_free_bytes=0,
    )
    hardware.initialize()
    with pytest.raises(ApplianceStartupError, match="no application update"):
        hardware.apply_update()
    hardware.close()
