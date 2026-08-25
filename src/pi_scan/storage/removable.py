"""Testable Linux removable-storage discovery and lifecycle operations."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Protocol, cast


@dataclass(frozen=True, slots=True)
class StorageVolume:
    device_path: Path
    filesystem: str
    label: str | None = None
    uuid: str | None = None
    size_bytes: int = 0
    available_bytes: int | None = None
    mount_points: tuple[Path, ...] = ()
    read_only: bool = False
    transport: str | None = None
    removable: bool = True

    @property
    def mounted(self) -> bool:
        return bool(self.mount_points)


class RemovableStorage(Protocol):
    def discover(self) -> tuple[StorageVolume, ...]: ...
    def mount(self, volume: StorageVolume) -> StorageVolume: ...
    def unmount(self, volume: StorageVolume, *, force: bool = False) -> None: ...
    def eject(self, volume: StorageVolume) -> None: ...


class StorageError(RuntimeError):
    pass


class StorageParseError(StorageError):
    pass


class StorageCommandError(StorageError):
    def __init__(self, command: Sequence[str], result: subprocess.CompletedProcess[str]) -> None:
        self.command = tuple(command)
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        super().__init__(f"{command[0]} exited with status {result.returncode}: {detail}")


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class LinuxRemovableStorage:
    """Discover with lsblk JSON; manage mounts through udisksctl."""

    _LSBLK = (
        "lsblk",
        "--json",
        "--bytes",
        "--list",
        "--output",
        "PATH,KNAME,PKNAME,TYPE,TRAN,RM,MOUNTPOINTS,FSTYPE,LABEL,UUID,SIZE,FSAVAIL,RO",
    )

    def __init__(self, *, runner: Runner | None = None, timeout: float = 15.0) -> None:
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("command timeout must be finite and positive")
        self._runner = runner or self._run
        self._timeout = timeout
        self._known_unmounted: set[tuple[Path, str | None]] = set()

    def discover(self) -> tuple[StorageVolume, ...]:
        result = self._checked(self._LSBLK)
        try:
            document = cast(object, json.loads(result.stdout))
            if not isinstance(document, dict):
                raise TypeError("top-level value is not an object")
            typed_document = cast(dict[str, object], document)
            raw_devices = typed_document["blockdevices"]
            if not isinstance(raw_devices, list):
                raise TypeError("blockdevices is not a list")
            rows = [
                cast(dict[str, object], raw_row)
                for raw_row in cast(list[object], raw_devices)
                if isinstance(raw_row, dict)
            ]
            transports = _resolve_transports(rows)
            volumes_list: list[StorageVolume] = []
            for row in rows:
                volume = _parse_volume(row, transport=transports.get(_device_name(row)))
                if volume is not None:
                    volumes_list.append(volume)
            volumes = tuple(volumes_list)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise StorageParseError(f"invalid lsblk JSON: {error}") from error
        return volumes

    def mount(self, volume: StorageVolume) -> StorageVolume:
        if volume.mounted:
            self._known_unmounted.discard(self._volume_key(volume))
            return volume
        self._checked(self._udisks_command("mount", volume))
        refreshed = self._find(volume)
        if not refreshed.mounted:
            raise StorageError(f"{volume.device_path} was not mounted after udisksctl succeeded")
        self._known_unmounted.discard(self._volume_key(refreshed))
        return refreshed

    def unmount(self, volume: StorageVolume, *, force: bool = False) -> None:
        command = list(self._udisks_command("unmount", volume))
        if force:
            command.append("--force")
        result = self._runner(command)
        # A filesystem that is already unmounted is the state the caller asked for.
        # Reporting it as a failure would abandon a part-finished ejection before
        # the drive had been powered off.
        if result.returncode and not _reports_not_mounted(result):
            raise StorageCommandError(command, result)
        self._known_unmounted.add(self._volume_key(volume))

    def eject(self, volume: StorageVolume) -> None:
        key = self._volume_key(volume)
        if volume.mounted and key not in self._known_unmounted:
            self.unmount(volume)
        self._checked(self._udisks_command("power-off", volume))
        self._known_unmounted.discard(key)

    @staticmethod
    def _volume_key(volume: StorageVolume) -> tuple[Path, str | None]:
        return volume.device_path, volume.uuid

    def _find(self, wanted: StorageVolume) -> StorageVolume:
        for candidate in self.discover():
            if wanted.uuid and candidate.uuid == wanted.uuid:
                return candidate
            if candidate.device_path == wanted.device_path:
                return candidate
        raise StorageError(f"storage volume disappeared: {wanted.device_path}")

    @staticmethod
    def _udisks_command(action: str, volume: StorageVolume) -> tuple[str, ...]:
        device = str(volume.device_path).replace("\\", "/")
        if device.startswith("/dev/") is False and device.startswith("/dev") is False:
            device = "/" + device.lstrip("/")
        return (
            "udisksctl",
            action,
            "--block-device",
            device,
            "--no-user-interaction",
        )

    def _checked(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = self._runner(command)
        if result.returncode:
            raise StorageCommandError(command, result)
        return result

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - fixed storage command argument vectors
                command, capture_output=True, text=True, timeout=self._timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StorageError(f"could not run {command[0]}: {error}") from error


class FakeRemovableStorage:
    """In-memory implementation for simulations without Linux block devices."""

    def __init__(self, volumes: Sequence[StorageVolume] = ()) -> None:
        self._volumes = {volume.device_path: volume for volume in volumes}

    def discover(self) -> tuple[StorageVolume, ...]:
        return tuple(self._volumes.values())

    def mount(self, volume: StorageVolume) -> StorageVolume:
        current = self._require(volume)
        if not current.mounted:
            mount_point = Path("/media/pi-scan") / current.device_path.name
            current = replace(current, mount_points=(mount_point,))
            self._volumes[current.device_path] = current
        return current

    def unmount(self, volume: StorageVolume, *, force: bool = False) -> None:
        current = self._require(volume)
        self._volumes[current.device_path] = replace(current, mount_points=())

    def eject(self, volume: StorageVolume) -> None:
        self._require(volume)
        self._volumes.pop(volume.device_path)

    def _require(self, volume: StorageVolume) -> StorageVolume:
        try:
            return self._volumes[volume.device_path]
        except KeyError as error:
            raise StorageError(f"unknown storage volume: {volume.device_path}") from error


PROTECTED_MOUNT_POINTS = frozenset(
    {
        Path("/"),
        Path("/boot"),
        Path("/boot/firmware"),
        Path("/etc"),
        Path("/home"),
        Path("/opt"),
        Path("/usr"),
        Path("/var"),
    }
)


def _is_system_volume(mounts: tuple[Path, ...]) -> bool:
    """Reject anything carrying the running system, whatever bus it arrived on."""
    return any(mount in PROTECTED_MOUNT_POINTS for mount in mounts)


def _device_name(row: dict[str, object]) -> str:
    kernel_name = str(row.get("kname") or "")
    return kernel_name or Path(str(row.get("path") or "")).name


def _resolve_transports(rows: Sequence[dict[str, object]]) -> dict[str, str]:
    """Give each device the transport of the disk that carries it.

    lsblk names a transport on the disk alone, so the filesystem of a USB stick
    arrives as a child partition with an empty TRAN. Without inheriting the
    disk's transport, 1.5's USB-only rule would reject every partitioned stick.
    """
    own: dict[str, str] = {}
    parents: dict[str, str] = {}
    for row in rows:
        name = _device_name(row)
        if not name:
            continue
        transport = str(row.get("tran") or "")
        if transport:
            own[name] = transport
        parent = str(row.get("pkname") or "")
        if parent and parent != name:
            parents[name] = parent
    resolved: dict[str, str] = {}
    for row in rows:
        name = _device_name(row)
        if not name:
            continue
        current = name
        seen: set[str] = set()
        while current not in own and current in parents and current not in seen:
            seen.add(current)
            current = parents[current]
        carrier = own.get(current)
        if carrier is not None:
            resolved[name] = carrier
    return resolved


def _parse_volume(row: dict[str, object], *, transport: str | None = None) -> StorageVolume | None:
    device_type = str(row.get("type") or "")
    filesystem = str(row.get("fstype") or "")
    raw_path = str(row.get("path") or "")
    path = Path(raw_path)
    transport = transport or str(row.get("tran") or "") or None
    # Pi Scan 1.5 accepted a volume only when UDisks reported its drive on the USB
    # bus, which excludes the appliance's own boot medium by construction.
    removable = transport == "usb"
    if (
        device_type not in {"disk", "part"}
        or not filesystem
        or not raw_path.startswith("/dev/")
        or not removable
    ):
        return None
    raw_mounts = row.get("mountpoints")
    if raw_mounts is None:
        mounts: tuple[Path, ...] = ()
    elif isinstance(raw_mounts, list):
        mounts = tuple(Path(str(item)) for item in cast(list[object], raw_mounts) if item)
    else:
        mounts = (Path(str(raw_mounts)),) if raw_mounts else ()
    if _is_system_volume(mounts):
        return None
    return StorageVolume(
        device_path=path,
        filesystem=filesystem,
        label=str(row["label"]) if row.get("label") is not None else None,
        uuid=str(row["uuid"]) if row.get("uuid") is not None else None,
        size_bytes=_as_int(row.get("size"), default=0) or 0,
        available_bytes=_as_int(row.get("fsavail"), default=None),
        mount_points=mounts,
        read_only=_as_bool(row.get("ro")),
        transport=transport,
        removable=removable,
    )


_NOT_MOUNTED = re.compile(r"not\s*mounted", re.IGNORECASE)


def _reports_not_mounted(result: subprocess.CompletedProcess[str]) -> bool:
    return any(_NOT_MOUNTED.search(stream) for stream in (result.stderr, result.stdout))


def _as_bool(value: object) -> bool:
    return (
        value is True
        or value == 1
        or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"})
    )


def _as_int(value: object, *, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    return int(str(value))
