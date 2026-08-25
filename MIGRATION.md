# Migrating from Pi Scan 1.5

This repository is the standalone Python 3.13 successor to the legacy Python 2 appliance.
Its Git history retains the legacy implementation, while the current tree contains only the
Python 3.13 product. Deployment uses `/opt/pi-scan` and does not overwrite an existing legacy
appliance checkout.

## Compatible data

- Existing `pi-scan.conf` camera positions, CHDK zoom values, and shutter values are loaded
  directly, provided the camera's serial number is unchanged. Configuration is keyed by
  serial exactly as in 1.5, and a camera that reports no serial is refused rather than
  filed under a port-derived key that would move with the USB socket.
- Existing numeric JPEG and supported RAW regular files reserve their page numbers. Numeric
  directories and symbolic links are ignored. A new session continues at the next even page.
- New captures retain four-digit page padding and use lowercase extensions. `.jpeg` is
  canonicalized to `.jpg`.

## Runtime differences

- CHDK support is mandatory and is always discovered in hardware mode.
- gPhoto2 is consulted only when no CHDK camera answers, as in 1.5, and may be disabled
  entirely with `--no-gphoto`. A session never mixes the two backends.
- Hardware mode requires exactly one removable filesystem on the USB bus with one absolute
  mount point. Volumes mounted at system paths are never eligible.
  Captures are stored under its `images` directory; configuration and diagnostics remain on
  that removable volume.
- Each camera must return a JPEG for the scanner preview; supported RAW files remain optional
  sidecars. Invalid, unsupported, symlinked, or RAW-only results are rejected before commit.
- Managed `images`, `debug`, preview, configuration, and event-log paths on removable media may
  not be symbolic links and must have the expected directory or regular-file type.
- Saving a new even/odd pair clears roles from disconnected cameras while retaining their zoom
  and shutter settings. Duplicate JSON keys are rejected as ambiguous.
- The GPIO21 active-low pedal is enabled by default. A GPIO failure leaves touch and
  keyboard input operational and produces a visible warning.
- The numeric keypad map is preserved per screen, and no digit captures a page. See the
  controls table in `README.md`.
- Diagnostics are written twice: `debug/events.jsonl` for machine reading and
  `debug/error.log` in the timestamped line format 1.5 used.
- Zoom steps use 1.5's rounding, so a camera reporting an odd number of zoom steps frames
  identically to the old appliance.
- Ejection is retried for 60 seconds and then forced, as in 1.5.
- `pi-scan-update-<major>.<minor>.archive` on the media is still offered as an update, but it
  must now contain one Pi Scan wheel, whose version must match the one the archive's name
  advertises. The wheel is installed into `/opt/pi-scan/venv` rather than unpacked over a
  source tree.
- Python 3.13 is required. The deployment templates install the application under
  `/opt/pi-scan` without overwriting a legacy source checkout.

## Current qualification boundary

The simulator, adapters, transaction logic, packaging, and deployment templates are covered
by automated tests. Physical Raspberry Pi and camera qualification is intentionally deferred.
Keep the legacy appliance available until the rewrite has been validated on the intended
scanner hardware.

The deployment templates assume an X11 session, as the 1.5 image provided. A Wayland session,
which current Raspberry Pi OS releases and therefore the Raspberry Pi 5 start by default, is
untested and unsupported. `deploy/README.md` describes what that migration would involve.
