import json
import subprocess
from pathlib import Path

import pytest

from pi_scan.storage import (
    FakeRemovableStorage,
    LinuxRemovableStorage,
    StorageCommandError,
    StorageParseError,
    StorageVolume,
)


def result(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def lsblk(*rows):
    return result(json.dumps({"blockdevices": rows}))


def test_discovery_filters_internal_and_non_filesystem_devices():
    rows = [
        {"path": "/dev/sda1", "type": "part", "tran": "sata", "rm": False, "fstype": "ext4"},
        {"path": "/dev/sdb", "type": "disk", "tran": "usb", "rm": True, "fstype": None},
        {
            "path": "/dev/sdb1",
            "type": "part",
            "tran": "usb",
            "rm": "1",
            "fstype": "exfat",
            "label": "SCANS",
            "uuid": "abc",
            "size": "1000",
            "fsavail": "750",
            "mountpoints": ["/media/SCANS", None],
            "ro": 0,
        },
    ]
    commands = []
    service = LinuxRemovableStorage(
        runner=lambda command: commands.append(tuple(command)) or lsblk(*rows)
    )
    assert service.discover() == (
        StorageVolume(
            Path("/dev/sdb1"),
            "exfat",
            "SCANS",
            "abc",
            1000,
            750,
            (Path("/media/SCANS"),),
            False,
            "usb",
            True,
        ),
    )
    assert commands[0][:4] == ("lsblk", "--json", "--bytes", "--list")


def usb_stick_rows():
    """What lsblk really prints for a partitioned stick beside the Pi's own disks."""
    return [
        {
            "path": "/dev/mmcblk0",
            "kname": "mmcblk0",
            "pkname": None,
            "type": "disk",
            "tran": None,
            "rm": False,
            "fstype": None,
            "mountpoints": [None],
        },
        {
            "path": "/dev/mmcblk0p2",
            "kname": "mmcblk0p2",
            "pkname": "mmcblk0",
            "type": "part",
            "tran": None,
            "rm": False,
            "fstype": "ext4",
            "mountpoints": ["/"],
        },
        {
            "path": "/dev/sda",
            "kname": "sda",
            "pkname": None,
            "type": "disk",
            "tran": "usb",
            "rm": True,
            "fstype": None,
            "size": "64000000000",
            "mountpoints": [None],
            "ro": False,
        },
        {
            "path": "/dev/sda1",
            "kname": "sda1",
            "pkname": "sda",
            "type": "part",
            "tran": None,
            "rm": True,
            "fstype": "exfat",
            "label": "SCANS",
            "uuid": "1234-ABCD",
            "size": "63900000000",
            "fsavail": "60000000000",
            "mountpoints": ["/media/pi/SCANS"],
            "ro": False,
        },
    ]


def test_a_partition_inherits_the_usb_transport_of_the_disk_that_carries_it():
    commands = []
    service = LinuxRemovableStorage(
        runner=lambda command: commands.append(tuple(command)) or lsblk(*usb_stick_rows())
    )
    assert service.discover() == (
        StorageVolume(
            Path("/dev/sda1"),
            "exfat",
            "SCANS",
            "1234-ABCD",
            63_900_000_000,
            60_000_000_000,
            (Path("/media/pi/SCANS"),),
            False,
            "usb",
            True,
        ),
    )
    assert "PKNAME" in commands[0][-1]


def test_a_partition_of_an_internal_disk_is_still_rejected():
    rows = [
        {
            "path": "/dev/nvme0n1",
            "kname": "nvme0n1",
            "pkname": None,
            "type": "disk",
            "tran": "nvme",
            "fstype": None,
        },
        {
            "path": "/dev/nvme0n1p1",
            "kname": "nvme0n1p1",
            "pkname": "nvme0n1",
            "type": "part",
            "tran": None,
            "fstype": "ext4",
            "mountpoints": ["/srv"],
        },
    ]
    service = LinuxRemovableStorage(runner=lambda command: lsblk(*rows))
    assert service.discover() == ()


def test_an_inherited_transport_does_not_admit_a_usb_disk_carrying_the_system():
    rows = [
        {
            "path": "/dev/sda",
            "kname": "sda",
            "pkname": None,
            "type": "disk",
            "tran": "usb",
            "fstype": None,
        },
        {
            "path": "/dev/sda1",
            "kname": "sda1",
            "pkname": "sda",
            "type": "part",
            "tran": None,
            "fstype": "vfat",
            "mountpoints": ["/boot/firmware"],
        },
    ]
    service = LinuxRemovableStorage(runner=lambda command: lsblk(*rows))
    assert service.discover() == ()


def test_a_parent_chain_is_walked_and_a_cycle_in_it_is_abandoned():
    rows = [
        {
            "path": "/dev/sdb1",
            "kname": "sdb1",
            "pkname": "sdb2",
            "type": "part",
            "tran": None,
            "fstype": "exfat",
        },
        {
            "path": "/dev/sdb2",
            "kname": "sdb2",
            "pkname": "sdb1",
            "type": "part",
            "tran": None,
            "fstype": "exfat",
        },
    ]
    service = LinuxRemovableStorage(runner=lambda command: lsblk(*rows))
    assert service.discover() == ()


def test_mount_uses_udisksctl_then_rediscovers_by_uuid():
    volume = StorageVolume(Path("/dev/sdb1"), "exfat", uuid="abc")
    responses = [
        result(),
        lsblk(
            {
                "path": "/dev/sdc1",
                "type": "part",
                "tran": "usb",
                "rm": True,
                "fstype": "exfat",
                "uuid": "abc",
                "mountpoints": ["/media/SCANS"],
            }
        ),
    ]
    commands = []

    def run(command):
        commands.append(tuple(command))
        return responses.pop(0)

    mounted = LinuxRemovableStorage(runner=run).mount(volume)
    assert mounted.mount_points == (Path("/media/SCANS"),)
    assert commands[0] == (
        "udisksctl",
        "mount",
        "--block-device",
        "/dev/sdb1",
        "--no-user-interaction",
    )


def test_unmount_force_and_eject_are_supervised_commands():
    volume = StorageVolume(Path("/dev/sdb1"), "exfat", mount_points=(Path("/mnt/x"),))
    commands = []
    service = LinuxRemovableStorage(
        runner=lambda command: commands.append(tuple(command)) or result()
    )
    service.unmount(volume, force=True)
    service.eject(volume)
    assert commands[0][-1] == "--force"
    assert commands[1][1] == "power-off"


def test_eject_retry_resumes_at_power_off_after_successful_unmount():
    volume = StorageVolume(
        Path("/dev/sdb1"),
        "exfat",
        uuid="scan-card",
        mount_points=(Path("/mnt/x"),),
    )
    responses = [result(), result(stderr="busy", returncode=1), result()]
    commands = []

    def run(command):
        commands.append(tuple(command))
        return responses.pop(0)

    service = LinuxRemovableStorage(runner=run)
    with pytest.raises(StorageCommandError, match="busy"):
        service.eject(volume)
    service.eject(volume)
    assert [command[1] for command in commands] == ["unmount", "power-off", "power-off"]


def test_unmounting_an_already_unmounted_filesystem_reports_success():
    volume = StorageVolume(Path("/dev/sdb1"), "exfat", mount_points=(Path("/mnt/x"),))
    responses = [result(stderr="Error unmounting /dev/sdb1: Not mounted", returncode=1), result()]
    commands = []

    def run(command):
        commands.append(tuple(command))
        return responses.pop(0)

    service = LinuxRemovableStorage(runner=run)
    service.unmount(volume, force=True)
    service.eject(volume)
    assert [command[1] for command in commands] == ["unmount", "power-off"]


def test_a_genuine_unmount_failure_is_still_raised():
    service = LinuxRemovableStorage(
        runner=lambda command: result(stderr="target is busy", returncode=1)
    )
    with pytest.raises(StorageCommandError, match="target is busy"):
        service.unmount(StorageVolume(Path("/dev/sdb1"), "exfat"))


def test_command_and_json_failures_are_typed():
    service = LinuxRemovableStorage(runner=lambda command: result(stderr="denied", returncode=1))
    with pytest.raises(StorageCommandError, match="denied"):
        service.discover()
    service = LinuxRemovableStorage(runner=lambda command: result("{broken"))
    with pytest.raises(StorageParseError):
        service.discover()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_linux_storage_rejects_invalid_command_timeout(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        LinuxRemovableStorage(timeout=timeout)


def test_fake_storage_mount_unmount_and_eject():
    volume = StorageVolume(Path("/dev/fake1"), "exfat")
    service = FakeRemovableStorage([volume])
    mounted = service.mount(volume)
    assert mounted.mounted
    service.unmount(mounted)
    assert not service.discover()[0].mounted
    service.eject(volume)
    assert service.discover() == ()
