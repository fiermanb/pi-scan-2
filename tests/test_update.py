"""Validation of update packages carried on the scan media."""

import zipfile
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from pi_scan.update import UpdateError, UpdatePackage, apply_update


def make_archive(root: Path, *members: str, version: str = "3.0") -> UpdatePackage:
    major, minor = version.split(".")
    archive = root / f"pi-scan-update-{version}.archive"
    with zipfile.ZipFile(archive, "w") as bundle:
        for member in members:
            bundle.writestr(member, "wheel")
    return UpdatePackage(archive, int(major), int(minor))


def succeed(command):
    return CompletedProcess(list(command), 0, "", "")


def refuse(command):
    raise AssertionError("no wheel may be installed when validation fails")


def test_a_matching_pi_scan_wheel_is_installed(tmp_path):
    package = make_archive(tmp_path, "pi_scan-3.0.0-py3-none-any.whl")
    assert apply_update(package, runner=succeed) == "pi_scan-3.0.0-py3-none-any.whl"


def test_an_escaped_project_name_is_matched_however_it_is_cased(tmp_path):
    package = make_archive(tmp_path, "Pi.Scan-3.0.0-py3-none-any.whl")
    assert apply_update(package, runner=succeed) == "Pi.Scan-3.0.0-py3-none-any.whl"


def test_an_unescaped_hyphen_in_the_project_name_is_refused(tmp_path):
    """A wheel escapes the hyphen to an underscore, so pi-scan-... is not one."""
    package = make_archive(tmp_path, "pi-scan-3.0.0-py3-none-any.whl")
    with pytest.raises(UpdateError, match="packages pi, not pi-scan"):
        apply_update(package, runner=refuse)


def test_a_wheel_with_a_build_tag_is_accepted(tmp_path):
    package = make_archive(tmp_path, "pi_scan-3.0.0-1-py3-none-any.whl")
    assert apply_update(package, runner=succeed) == "pi_scan-3.0.0-1-py3-none-any.whl"


def test_a_wheel_for_another_project_is_refused(tmp_path):
    package = make_archive(tmp_path, "requests-3.0.0-py3-none-any.whl")
    with pytest.raises(UpdateError, match="packages requests, not pi-scan"):
        apply_update(package, runner=refuse)


def test_a_wheel_whose_name_only_starts_like_pi_scan_is_refused(tmp_path):
    package = make_archive(tmp_path, "pi_scanner-3.0.0-py3-none-any.whl")
    with pytest.raises(UpdateError, match="packages pi-scanner, not pi-scan"):
        apply_update(package, runner=refuse)


@pytest.mark.parametrize(
    "member",
    [
        "pi_scan.whl",
        "pi_scan-3.0.0.whl",
        "pi_scan-3.0.0-py3-none.whl",
        "-3.0.0-py3-none-any.whl",
    ],
)
def test_a_malformed_wheel_name_is_refused(tmp_path, member):
    package = make_archive(tmp_path, member)
    with pytest.raises(UpdateError, match="not a valid wheel file name"):
        apply_update(package, runner=refuse)


def test_a_wheel_without_a_readable_version_is_refused(tmp_path):
    package = make_archive(tmp_path, "pi_scan-alpha-py3-none-any.whl")
    with pytest.raises(UpdateError, match="does not carry a readable version"):
        apply_update(package, runner=refuse)


@pytest.mark.parametrize("wheel_version", ["2.0.0", "3.1.0", "4.0.0", "30.0.0"])
def test_a_wheel_that_disagrees_with_the_archive_version_is_refused(tmp_path, wheel_version):
    package = make_archive(tmp_path, f"pi_scan-{wheel_version}-py3-none-any.whl")
    with pytest.raises(UpdateError, match="advertises 3.0"):
        apply_update(package, runner=refuse)


def test_a_patch_release_of_the_advertised_version_is_accepted(tmp_path):
    package = make_archive(tmp_path, "pi_scan-3.0.7-py3-none-any.whl")
    assert apply_update(package, runner=succeed) == "pi_scan-3.0.7-py3-none-any.whl"


def test_an_archive_carrying_two_wheels_is_refused(tmp_path):
    package = make_archive(
        tmp_path, "pi_scan-3.0.0-py3-none-any.whl", "pi_scan-3.0.1-py3-none-any.whl"
    )
    with pytest.raises(UpdateError, match="exactly one wheel"):
        apply_update(package, runner=refuse)
