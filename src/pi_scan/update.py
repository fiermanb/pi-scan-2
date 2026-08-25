"""In-field application updates carried on the scan media.

Pi Scan 1.5 looked for ``pi-scan-update-<major>.<minor>.archive`` on the mounted
stick and unpacked it over the running application, which is how sites without a
network updated their scanners. The same file name and version rule are kept
here; only the installation step changed, because the application is now a wheel
in a virtual environment rather than a source tree in a home directory.

Applying an update runs code that arrived on removable media. That was true of
1.5 as well, and it is why applying one is always an explicit operator action
and never automatic. The wheel inside the archive must name this project and the
version the archive advertises, which stops an unrelated or mislabelled wheel
being installed over the application. A file name is not a signature, so that
check narrows the mistake, not the attack; a signed or hashed manifest remains
future work.
"""

import re
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

_UPDATE_NAME = re.compile(r"^pi-scan-update-(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.archive$")
_VERSION = re.compile(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)")
_WHEEL_NAME = re.compile(
    r"^(?P<project>[^-]+)-(?P<version>[^-]+)"
    r"(?:-(?P<build>[0-9][^-]*))?"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)
_PROJECT = "pi-scan"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """An update package on the scan media could not be applied."""


@dataclass(frozen=True, slots=True)
class UpdatePackage:
    path: Path
    major: int
    minor: int

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}"


def parse_version(version: str) -> tuple[int, int]:
    match = _VERSION.match(version)
    if match is None:
        raise UpdateError(f"unreadable application version: {version!r}")
    return int(match.group("major")), int(match.group("minor"))


def find_update(media_root: Path, current_version: str) -> UpdatePackage | None:
    """Return the newest update on the media that is newer than what is running."""
    current = parse_version(current_version)
    best: UpdatePackage | None = None
    if not media_root.is_dir():
        return None
    for candidate in media_root.iterdir():
        match = _UPDATE_NAME.fullmatch(candidate.name)
        if match is None or candidate.is_symlink() or not candidate.is_file():
            continue
        version = int(match.group("major")), int(match.group("minor"))
        if version <= current:
            continue
        if best is None or version > (best.major, best.minor):
            best = UpdatePackage(candidate, version[0], version[1])
    return best


def apply_update(
    package: UpdatePackage,
    *,
    python_executable: str | None = None,
    runner: Runner | None = None,
) -> str:
    """Install the wheel carried by an update archive into the running environment."""
    executable = python_executable or sys.executable
    run = runner or _run
    with TemporaryDirectory(prefix="pi-scan-update-") as directory:
        wheel = _extract_wheel(package, Path(directory))
        result = run(
            [
                executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--force-reinstall",
                "--no-deps",
                str(wheel),
            ]
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise UpdateError(f"could not install {wheel.name}: {detail}")
        return wheel.name


def _extract_wheel(package: UpdatePackage, destination: Path) -> Path:
    archive = package.path
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = [
                name
                for name in bundle.namelist()
                if name.lower().endswith(".whl") and "/" not in name and not name.startswith("..")
            ]
            if len(names) != 1:
                raise UpdateError(
                    f"{archive.name} must contain exactly one wheel at its top level; "
                    f"found {len(names)}"
                )
            _check_wheel_name(names[0], package)
            bundle.extract(names[0], destination)
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError(f"could not read {archive.name}: {error}") from error
    return destination / names[0]


def _check_wheel_name(name: str, package: UpdatePackage) -> None:
    """Refuse a wheel that is not the Pi Scan release the archive advertises.

    The file name is the only identity an unsigned archive carries, so an archive
    that names one version while carrying another, or that carries some other
    project entirely, is rejected rather than installed over the application.
    """
    parsed = _WHEEL_NAME.fullmatch(name)
    if parsed is None:
        raise UpdateError(f"{name} is not a valid wheel file name")
    project = _normalized_project(parsed.group("project"))
    if project != _PROJECT:
        raise UpdateError(f"{name} packages {project}, not {_PROJECT}")
    version = parsed.group("version")
    numbered = _VERSION.match(version)
    if numbered is None:
        raise UpdateError(f"{name} does not carry a readable version")
    if (int(numbered.group("major")), int(numbered.group("minor"))) != (
        package.major,
        package.minor,
    ):
        raise UpdateError(
            f"{name} carries version {version}, "
            f"but {package.path.name} advertises {package.version}"
        )


def _normalized_project(project: str) -> str:
    """Normalize a wheel's project name as PEP 503 does, so pi_scan matches pi-scan."""
    return re.sub(r"[-_.]+", "-", project).lower()


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argument vector, operator-initiated
            command, capture_output=True, text=True, timeout=600, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(f"could not run {command[0]}: {error}") from error
