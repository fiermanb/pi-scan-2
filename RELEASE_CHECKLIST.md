# Python 3.13 rewrite release checklist

## Automated software gates

Run from the repository root in a Python 3.13 development environment:

```text
ruff format --check src tests
ruff check src tests
pyright
python -W error -m pytest --cov=pi_scan --cov-report=term-missing -q
python -m compileall -q src/pi_scan tests
python -m pip check
python -m build
sh -n deploy/install.sh
```

The core coverage floor is 90%. The dynamic Kivy shell and the two-line module launcher are
excluded; their import and CLI boundaries remain covered by tests.
Git attributes pin Python, CI, shell, systemd, udev, environment, and documentation files to
LF line endings so Linux deployment files remain executable after Windows development.

## Artifact checks

- Confirm the wheel and source archive both report the intended version.
- Install both the wheel and source distribution in a clean Python 3.13 environment.
- Run `pi-scan-sim --version` and `pi-scan-ui --version` from each installed artifact.
- Run one JPEG+DNG simulator capture from each installed artifact.
- Confirm pages `0000` and `0001` each have a non-empty JPEG and DNG in both outputs.
- Confirm the source archive contains `deploy/install.sh`, `deploy/pi-scan.service`,
  `MIGRATION.md`, and `CHANGELOG.md`.
- Confirm legacy top-level Python 2 modules are absent from the wheel.
- Confirm the installer checks for Python 3.13, mandatory `chdkptp`, `lsblk`, and `udisksctl`.

## Release decisions

- Release version selected: `2.0.0`.
- Keep CHDK support mandatory in hardware mode.
- Review `MIGRATION.md`, `CHANGELOG.md`, and the physical-hardware limitation text.
- Review the complete Git diff before staging.
- Commit the rewrite, push the selected fork/branch, and tag only after version approval.

## Deferred hardware qualification

Raspberry Pi, GPIO, UDisks authorization, Kivy display-manager startup, and physical CHDK or
gPhoto2 camera qualification are intentionally deferred. This is a documented release
limitation, not a passed gate. Keep the legacy appliance available for production scanners
until qualification is performed on the intended hardware.

The deployment templates assume an X11 session. Wayland and `labwc` startup, and therefore
the Raspberry Pi 5 and current Raspberry Pi OS releases, are part of the same deferred
qualification and must not be described as supported until they have been run on hardware.
