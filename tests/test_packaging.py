import tomllib
from pathlib import Path

import pi_scan

ROOT = Path(__file__).parents[1]


def test_runtime_and_package_versions_match():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pi_scan.__version__ == metadata["project"]["version"]
    assert metadata["project"]["requires-python"] == ">=3.13"
    assert metadata["project"]["dependencies"] == ["Pillow>=11,<13"]
    assert metadata["project"]["license"] == "BSD-2-Clause"
    assert metadata["project"]["license-files"] == ["LICENSE"]


def test_python313_tooling_and_optional_dependency_boundaries():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["optional-dependencies"]["ui"] == ["kivy>=2.3.1,<3"]
    assert metadata["project"]["optional-dependencies"]["hardware"] == ["gpiozero>=2,<3"]
    assert metadata["tool"]["ruff"]["target-version"] == "py313"
    assert "S" in metadata["tool"]["ruff"]["lint"]["select"]
    assert metadata["tool"]["ruff"]["lint"]["per-file-ignores"]["tests/**/*.py"] == ["S101"]
    assert metadata["tool"]["pyright"]["pythonVersion"] == "3.13"


def test_wheel_configuration_only_packages_python3_rewrite():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_find = metadata["tool"]["setuptools"]["packages"]["find"]
    assert package_find["where"] == ["src"]
    assert package_find["include"] == ["pi_scan*"]
    assert metadata["project"]["scripts"] == {
        "pi-scan-sim": "pi_scan.simulator:main",
        "pi-scan-ui": "pi_scan.ui.kivy_app:main",
    }
    assert metadata["tool"]["setuptools"]["data-files"]["share/doc/pi-scan"] == [
        "CHANGELOG.md",
        "MIGRATION.md",
        "RELEASE_CHECKLIST.md",
    ]


def test_core_coverage_gate_excludes_only_dynamic_shell_boundaries():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["tool"]["coverage"]["report"]["fail_under"] == 90
    assert metadata["tool"]["coverage"]["run"]["omit"] == [
        "*/pi_scan/__main__.py",
        "*/pi_scan/ui/kivy_app.py",
    ]
