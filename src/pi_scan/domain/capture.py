"""Transactional coordination of an even/odd camera capture pair."""

import os
import shutil
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pi_scan.cameras.base import CAPTURE_IMAGE_SUFFIXES, Camera


class CapturePairError(RuntimeError):
    """Raised when a pair cannot be captured and committed completely."""

    def __init__(
        self,
        message: str,
        failures: dict[str, Exception] | None = None,
        *,
        recovery_directory: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.failures = failures or {}
        self.recovery_directory = recovery_directory


@dataclass(frozen=True, slots=True)
class CapturePairResult:
    even_page: int
    odd_page: int
    even_files: tuple[Path, ...]
    odd_files: tuple[Path, ...]


class CaptureCoordinator:
    """Capture both pages concurrently and publish the pair as one transaction."""

    def __init__(
        self,
        even_camera: Camera,
        odd_camera: Camera,
        *,
        file_sync: Callable[[Path], None] | None = None,
        directory_sync: Callable[[Path], None] | None = None,
    ) -> None:
        self.even_camera = even_camera
        self.odd_camera = odd_camera
        self._file_sync = file_sync or _sync_file
        self._directory_sync = directory_sync or _sync_directory

    def capture_pair(self, image_directory: Path, even_page: int) -> CapturePairResult:
        if even_page < 0 or even_page % 2:
            raise ValueError("even_page must be a non-negative even number")

        image_directory.mkdir(parents=True, exist_ok=True)
        staging = image_directory / f".pi-scan-capture-{uuid4().hex}"
        staging.mkdir()
        cameras = {"even": self.even_camera, "odd": self.odd_camera}

        preserve_staging = False
        try:
            captures = self._capture_to_staging(cameras, staging)
            targets = {
                "even": self._targets(
                    captures["even"], staging / "even", image_directory, even_page
                ),
                "odd": self._targets(
                    captures["odd"], staging / "odd", image_directory, even_page + 1
                ),
            }
            self._commit(tuple(targets["even"] + targets["odd"]), staging)
            return CapturePairResult(
                even_page=even_page,
                odd_page=even_page + 1,
                even_files=tuple(target for _, target in targets["even"]),
                odd_files=tuple(target for _, target in targets["odd"]),
            )
        except CapturePairError as error:
            preserve_staging = error.recovery_directory is not None
            raise
        finally:
            if not preserve_staging:
                shutil.rmtree(staging, ignore_errors=True)

    def _capture_to_staging(
        self, cameras: dict[str, Camera], staging: Path
    ) -> dict[str, Sequence[Path]]:
        captures: dict[str, Sequence[Path]] = {}
        failures: dict[str, Exception] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pi-scan-camera") as executor:
            futures = {
                executor.submit(camera.capture, staging / side): side
                for side, camera in cameras.items()
            }
            for future in as_completed(futures):
                side = futures[future]
                try:
                    captures[side] = future.result()
                except Exception as error:
                    failures[side] = error

        if failures:
            raise CapturePairError("one or more cameras failed", failures)
        for side, paths in captures.items():
            self._validate_artifacts(side, staging / side, paths)
        return captures

    @staticmethod
    def _validate_artifacts(side: str, base: Path, paths: Sequence[Path]) -> None:
        if not paths:
            raise CapturePairError(f"{side} camera returned no files")
        unsupported = [
            path.suffix for path in paths if path.suffix.lower() not in CAPTURE_IMAGE_SUFFIXES
        ]
        if unsupported:
            raise CapturePairError(
                f"{side} camera returned unsupported file type: {unsupported[0] or '<none>'}"
            )
        suffixes = [_published_suffix(path) for path in paths]
        if ".jpg" not in suffixes:
            raise CapturePairError(f"{side} camera returned no JPEG image")
        if len(set(suffixes)) != len(suffixes):
            raise CapturePairError(f"{side} camera returned duplicate file types")
        if len(set(paths)) != len(paths):
            raise CapturePairError(f"{side} camera returned duplicate files")
        for path in paths:
            if path.parent != base.parent or not path.name.startswith(base.name + "."):
                raise CapturePairError(f"{side} camera returned an unexpected path: {path}")
            if path.is_symlink():
                raise CapturePairError(f"{side} camera returned a symbolic link: {path}")
            if not path.is_file() or path.stat().st_size == 0:
                raise CapturePairError(f"{side} camera returned an empty or missing file: {path}")

    @staticmethod
    def _targets(
        paths: Sequence[Path], base: Path, output: Path, page: int
    ) -> list[tuple[Path, Path]]:
        return [(path, output / f"{page:04d}{_published_suffix(path)}") for path in paths]

    def _commit(self, files: tuple[tuple[Path, Path], ...], staging: Path) -> None:
        backup_directory = staging / "backups"
        backup_directory.mkdir()
        backups: list[tuple[Path, Path]] = []
        committed: list[Path] = []
        try:
            for source, _ in files:
                self._file_sync(source)
            for _, target in files:
                if target.exists():
                    backup = backup_directory / f"{len(backups)}-{target.name}"
                    os.replace(target, backup)
                    backups.append((backup, target))
            for source, target in files:
                os.replace(source, target)
                committed.append(target)
            self._directory_sync(files[0][1].parent)
        except BaseException as error:
            rollback_failures: dict[str, Exception] = {}
            for target in reversed(committed):
                try:
                    target.unlink(missing_ok=True)
                except OSError as rollback_error:
                    rollback_failures[f"remove:{target.name}"] = rollback_error
            for backup, target in reversed(backups):
                try:
                    os.replace(backup, target)
                except OSError as rollback_error:
                    rollback_failures[f"restore:{target.name}"] = rollback_error
            with suppress(OSError):
                self._directory_sync(files[0][1].parent)
            if rollback_failures:
                raise CapturePairError(
                    f"failed to commit captured files and rollback was incomplete; "
                    f"recovery files preserved at {staging}",
                    rollback_failures,
                    recovery_directory=staging,
                ) from error
            raise CapturePairError("failed to commit captured files") from error


def _sync_file(path: Path) -> None:
    """Flush file contents before its staging name is atomically published."""
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _published_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def _sync_directory(path: Path) -> None:
    """Flush directory entries on POSIX; Windows has no equivalent directory handle."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
