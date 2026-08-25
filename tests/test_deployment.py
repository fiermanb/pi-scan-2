from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_linux_and_python_files_are_pinned_to_lf_line_endings():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert ".gitattributes text eol=lf" in attributes
    assert ".gitignore text eol=lf" in attributes
    for pattern in ("*.py", "*.toml", "*.yml", "*.sh", "*.service", "*.rules", "*.env"):
        assert f"{pattern} text eol=lf" in attributes


def test_systemd_unit_runs_hardware_mode_as_unprivileged_user():
    unit = (ROOT / "deploy" / "pi-scan.service").read_text(encoding="utf-8")
    assert "User=@PI_SCAN_USER@" in unit
    assert "Group=pi-scan" in unit
    assert "Group=@PI_SCAN_USER@" not in unit
    assert "SupplementaryGroups=gpio plugdev" in unit
    assert "ExecStart=/opt/pi-scan/venv/bin/pi-scan-ui --hardware" in unit
    assert "Environment=XAUTHORITY=%h/.Xauthority" in unit
    assert "Restart=on-failure" in unit
    assert "Restart=always" not in unit


def test_deployment_templates_declare_the_x11_assumption():
    environment = (ROOT / "deploy" / "pi-scan.env").read_text(encoding="utf-8")
    assert "DISPLAY=:0" in environment
    assert "Wayland" in environment
    unit = (ROOT / "deploy" / "pi-scan.service").read_text(encoding="utf-8")
    assert "X11 session assumed" in unit
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "## Display server" in readme
    assert "labwc" in readme
    assert "None of this has been run" in readme
    assert "Raspberry Pi 5" in readme


# Any of these says the same thing; pinning one spelling would fail the next
# time a document is rewritten in different words rather than made less honest.
QUALIFICATION_CAVEATS = (
    "untested",
    "deferred",
    "not been qualified",
    "not yet been qualified",
    "remains to be completed",
    "not claimed",
)

# README.md is an overview of how the software is meant to run, not a status
# report, so the deferral is recorded in the deployment and release documents.
DEPLOYMENT_DOCUMENTS = ("MIGRATION.md", "RELEASE_CHECKLIST.md", "deploy/README.md")


def test_no_document_claims_wayland_or_raspberry_pi_5_support():
    for name in DEPLOYMENT_DOCUMENTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "Wayland" in text, name
        assert "labwc" in text or "Raspberry Pi 5" in text, name
        assert any(caveat in text for caveat in QUALIFICATION_CAVEATS), name


def test_udev_rules_are_group_scoped_not_world_writable():
    rules = (ROOT / "deploy" / "99-pi-scan.rules").read_text(encoding="utf-8")
    assert 'GROUP="pi-scan"' in rules
    assert 'MODE="0660"' in rules
    assert 'MODE="0666"' not in rules
    assert 'ATTR{idVendor}=="04a9"' in rules
    assert 'ENV{ID_GPHOTO2}=="1"' in rules


def test_installer_does_not_enable_service_before_manual_policy_review():
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert 'if [ "$(id -u)" -ne 0 ]' in installer
    assert "*[!A-Za-z0-9_.-]*)" in installer
    assert 'echo "invalid appliance user name:' in installer
    assert "for required_command in python3.13 chdkptp lsblk udisksctl" in installer
    assert 'command -v "$required_command"' in installer
    assert "python3.13 -m venv" in installer
    assert 'pi-scan.service" \\\n    > /etc/systemd/system/pi-scan.service' in installer
    assert '99-pi-scan.rules" \\\n    /etc/udev/rules.d/99-pi-scan.rules' in installer
    assert 'pi-scan.service" +' not in installer
    assert '99-pi-scan.rules" +' not in installer
    assert installer.count("systemctl enable --now") == 1
    assert 'echo "Then enable it with:' in installer


def test_python313_ci_checks_rewrite_without_parsing_legacy_python2():
    workflow = (ROOT / ".github" / "workflows" / "python313.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.13"' in workflow
    assert "ruff check src tests" in workflow
    assert "ruff format --check src tests" in workflow
    assert "run: pyright" in workflow
    assert "sh -n deploy/install.sh" in workflow
    assert "python -W error -m pytest --cov=pi_scan --cov-report=term-missing -q" in workflow
    assert "compileall -q src/pi_scan tests" in workflow
    assert "python -m build" in workflow
    assert "tar -tzf dist/*.tar.gz | grep -q '/deploy/install.sh$'" in workflow
    assert "tar -tzf dist/*.tar.gz | grep -q '/deploy/pi-scan.service$'" in workflow
    assert "tar -tzf dist/*.tar.gz | grep -q '/MIGRATION.md$'" in workflow
    assert "pip install --force-reinstall --no-deps dist/*.whl" in workflow
    assert "pi-scan-sim --output wheel-smoke-scans --pairs 1 --dng --quiet" in workflow
    assert "test -s wheel-smoke-scans/0000.jpg" in workflow
    assert "test -s wheel-smoke-scans/0001.dng" in workflow
    assert "pip install --force-reinstall --no-deps dist/*.tar.gz" in workflow
    assert "pi-scan-sim --output sdist-smoke-scans --pairs 1 --dng --quiet" in workflow
    assert "test -s sdist-smoke-scans/0000.jpg" in workflow
    assert "test -s sdist-smoke-scans/0001.dng" in workflow


def test_source_manifest_includes_appliance_deployment_templates():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include CHANGELOG.md" in manifest
    assert "include MIGRATION.md" in manifest
    assert "include RELEASE_CHECKLIST.md" in manifest
    assert "recursive-include deploy *" in manifest


def test_installation_guide_states_the_preconditions_the_installer_enforces():
    guide = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    for command in ("python3.13", "chdkptp", "lsblk", "udisksctl"):
        assert command in installer
        assert command in guide
    for path in ("/opt/pi-scan/venv", "/etc/default/pi-scan", "/etc/udev/rules.d/99-pi-scan.rules"):
        assert path in guide


def test_installation_guide_does_not_promise_untested_hardware():
    guide = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    assert "Wayland" in guide
    assert "untested" in guide
    assert "Raspberry Pi 5" in guide
    assert "Physical qualification" in guide


def test_installation_guide_ships_with_the_source_distribution():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include INSTALL.md" in manifest
    workflow = (ROOT / ".github" / "workflows" / "python313.yml").read_text(encoding="utf-8")
    assert "/INSTALL.md$" in workflow


def test_installer_defaults_to_the_account_that_invoked_sudo():
    """Raspberry Pi OS has created no default 'pi' account since April 2022."""
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert 'pi_scan_user="${1:-${SUDO_USER:-pi}}"' in installer
    assert 'pi_scan_user="${1:-pi}"' not in installer
    assert "no default 'pi' account since April 2022" in installer


def test_installer_refuses_to_run_the_appliance_as_root():
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert '[ "$pi_scan_user" = "root" ]' in installer
    assert "the appliance must not run as root" in installer


def test_installer_replaces_the_application_when_the_version_is_unchanged():
    """A rebuilt test tree must reach the scanner, and its version does not move."""
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert '-m pip install --force-reinstall --no-deps "$repo_root"' in installer


def test_documented_install_command_matches_the_installer_default():
    for name in (ROOT / "INSTALL.md", ROOT / "README.md", ROOT / "deploy" / "README.md"):
        text = name.read_text(encoding="utf-8")
        assert "sudo sh deploy/install.sh" in text
        assert "sudo sh deploy/install.sh pi" not in text
