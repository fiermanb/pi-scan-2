"""Validated configuration and migration from the Pi Scan 1.5 JSON format."""

import json
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

LEGACY_ZOOM_SEQUENCE = (
    "Min Zoom",
    *(f"{half / 2:g}" for half in range(1, 20)),
    "Max Zoom",
)
LEGACY_ZOOM_CHOICES = frozenset(LEGACY_ZOOM_SEQUENCE)
LEGACY_SHUTTER_SEQUENCE = (
    "1/250",
    "1/125",
    "1/60",
    "1/30",
    "1/25",
    "1/20",
    "1/18",
    "1/16",
    "1/15",
    "1/14",
    "1/12",
    "1/10",
    "1/8",
    "1/6",
    "1/4",
    "1/3",
    "1/2",
    "1/1",
)
LEGACY_SHUTTER_CHOICES = frozenset(LEGACY_SHUTTER_SEQUENCE)


class ConfigurationError(ValueError):
    """Configuration content is malformed or unsupported."""


class CameraSide(StrEnum):
    EVEN = "even"
    ODD = "odd"


@dataclass(frozen=True, slots=True)
class CameraConfiguration:
    position: CameraSide | None = None
    zoom: str = "5"
    shutter: str = "1/15"


@dataclass(frozen=True, slots=True)
class ScannerConfiguration:
    cameras: dict[str, CameraConfiguration]

    def camera(self, identifier: str) -> CameraConfiguration:
        return self.cameras.get(identifier, CameraConfiguration())

    def with_camera_positions(
        self, *, even_identifier: str, odd_identifier: str
    ) -> "ScannerConfiguration":
        if not even_identifier or not odd_identifier:
            raise ConfigurationError("camera identifiers must be non-empty")
        if even_identifier == odd_identifier:
            raise ConfigurationError("even and odd cameras must be distinct")
        cameras = {
            identifier: replace(camera, position=None)
            for identifier, camera in self.cameras.items()
        }
        cameras[even_identifier] = replace(self.camera(even_identifier), position=CameraSide.EVEN)
        cameras[odd_identifier] = replace(self.camera(odd_identifier), position=CameraSide.ODD)
        return ScannerConfiguration(cameras)

    def with_camera_settings(
        self,
        identifier: str,
        *,
        zoom: str | None = None,
        shutter: str | None = None,
    ) -> "ScannerConfiguration":
        if not identifier:
            raise ConfigurationError("camera identifier must be non-empty")
        current = self.camera(identifier)
        selected_zoom = current.zoom if zoom is None else zoom
        selected_shutter = current.shutter if shutter is None else shutter
        if selected_zoom not in LEGACY_ZOOM_CHOICES:
            raise ConfigurationError(f"camera {identifier!r} has invalid zoom {selected_zoom!r}")
        if selected_shutter not in LEGACY_SHUTTER_CHOICES:
            raise ConfigurationError(
                f"camera {identifier!r} has invalid shutter {selected_shutter!r}"
            )
        cameras = dict(self.cameras)
        cameras[identifier] = replace(
            current,
            zoom=selected_zoom,
            shutter=selected_shutter,
        )
        return ScannerConfiguration(cameras)


def parse_legacy_configuration(text: str) -> ScannerConfiguration:
    try:
        raw = cast(object, json.loads(text, object_pairs_hook=_unique_json_object))
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"invalid JSON: {error.msg}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("top-level configuration must be an object")

    cameras: dict[str, CameraConfiguration] = {}
    typed_raw = cast(dict[object, object], raw)
    for identifier, value in typed_raw.items():
        if not isinstance(identifier, str) or not identifier:
            raise ConfigurationError("camera identifiers must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigurationError(f"configuration for {identifier!r} must be an object")
        cameras[identifier] = _parse_legacy_camera(identifier, cast(dict[str, Any], value))
    return ScannerConfiguration(cameras)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_legacy_configuration(path: Path) -> ScannerConfiguration:
    return parse_legacy_configuration(path.read_text(encoding="utf-8"))


def serialize_legacy_configuration(configuration: ScannerConfiguration) -> str:
    raw = {
        identifier: {
            **({"position": camera.position.value} if camera.position is not None else {}),
            "shutter": camera.shutter,
            "zoom": camera.zoom,
        }
        for identifier, camera in sorted(configuration.cameras.items())
    }
    return json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_legacy_configuration(path: Path, configuration: ScannerConfiguration) -> None:
    """Atomically replace a legacy-compatible configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    backup = path.with_name(f".{path.name}.backup-{uuid4().hex}")
    backup_created = False
    published = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialize_legacy_configuration(configuration))
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copyfile(path, backup)
            _sync_file(backup)
            backup_created = True
        os.replace(temporary, path)
        published = True
        _sync_directory(path.parent)
        if backup_created:
            backup.unlink()
            with suppress(OSError):
                _sync_directory(path.parent)
    except Exception:
        if published:
            if backup_created:
                os.replace(backup, path)
            else:
                path.unlink(missing_ok=True)
            with suppress(OSError):
                _sync_directory(path.parent)
        elif backup_created:
            backup.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _parse_legacy_camera(identifier: str, raw: dict[str, Any]) -> CameraConfiguration:
    position_value = raw.get("position")
    try:
        position = CameraSide(position_value) if position_value is not None else None
    except ValueError as error:
        raise ConfigurationError(
            f"camera {identifier!r} has invalid position {position_value!r}"
        ) from error

    zoom = raw.get("zoom", "5")
    shutter = raw.get("shutter", "1/15")
    if not isinstance(zoom, str) or zoom not in LEGACY_ZOOM_CHOICES:
        raise ConfigurationError(f"camera {identifier!r} has invalid zoom {zoom!r}")
    if not isinstance(shutter, str) or shutter not in LEGACY_SHUTTER_CHOICES:
        raise ConfigurationError(f"camera {identifier!r} has invalid shutter {shutter!r}")
    return CameraConfiguration(position=position, zoom=zoom, shutter=shutter)
