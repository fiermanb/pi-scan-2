# Changelog

## 2.0.0

### Verified against Pi Scan 1.5

A behaviour-level comparison against the 1.5 tree was carried out before release, and the
differences it found were resolved in favour of the original appliance:

- Restored the per-screen numeric keypad map, so a keypad-only console remains operable. As
  in 1.5, no digit captures a page.
- Restored full-resolution focus checking. Zooming past 1:1 now shows an unscaled window cut
  from the captured page instead of magnifying the fitted preview.
- Restricted removable-storage selection to the USB bus as 1.5 did, and excluded volumes
  mounted at system paths, so the appliance can never select or power off its own disk.
- Corrected the CHDK speed argument: the market ISO is converted to APEX96 and passed as
  `svm`, matching `util.iso_to_sv96` as used by 1.5.
- Keyed camera configuration by serial number alone, refusing cameras that report none
  rather than using a port-derived identifier that moves between USB sockets.
- Restored 1.5's zoom-step rounding, which differs from Python 3's on exact halves.
- Restored the audible failure tone on both cameras, the camera power-off control, the
  `debug/error.log` diagnostic file, forced ejection after a 60-second grace period, and
  updates carried on the scan media.
- Restored 1.5's discovery order, where gPhoto2 is consulted only if no CHDK camera answers,
  so a session cannot mix the two backends.

### The rewrite

- Replaced the legacy working tree with the standalone Python 3.13 `pi_scan` package while
  retaining the original repository history.
- Added mandatory CHDK discovery, configuration, capture, DNG, test-shot, and ROM-log support.
- Added optional gPhoto2 discovery and JPEG/RAW capture support, used only when no CHDK
  camera answers.
- Added transactional paired capture, durable rescan rollback, restart-safe numbering, and
  removable-media capacity and lifecycle management.
- Hardened capture publication to reject symbolic links, unsupported formats, and RAW-only
  camera results before files are committed or page numbering advances.
- Made restart numbering count only supported regular capture files, ignoring numeric-looking
  directories and symbolic links.
- Hardened removable-media startup with finite deadlines, one absolute mount-point requirement,
  durable write probing, managed-path symlink/type rejection, and safe ejection after validation
  failures.
- Added strict duplicate-device and stable-identity handling, finite camera command deadlines,
  typed process-launch failures, and primary-error preservation during CHDK diagnostic cleanup.
- Added simulator and Kivy appliance modes, GPIO21 pedal support, structured diagnostics,
  persistent hardware warnings, and recovery controls.
- Added Raspberry Pi deployment templates and Python 3.13 CI covering Ruff, strict Pyright,
  pytest, byte-compilation, installer syntax, standard artifact construction, and an
  installed wheel and source-distribution simulator smoke test.
- Included appliance deployment templates in the source distribution.
- Enforced a 90% coverage floor for the framework-neutral rewrite core.
- Promoted Python warnings and Ruff security rules to enforced CI failures.
- Added a reproducible release checklist with deferred hardware qualification clearly marked.
- Pinned source and Linux deployment files to LF line endings across platforms.
- Added hardware-free `--version` reporting to both packaged console commands.
- Rejected duplicate legacy JSON keys and cleared stale camera-side roles when persisting a new
  pair without discarding disconnected-camera zoom or shutter settings.
- Added installer preflight checks for Python 3.13, mandatory `chdkptp`, `lsblk`, and
  `udisksctl`, validated the interpolated appliance username, and removed assumptions about
  same-named user groups in the systemd unit.
- Made hardware Finish include safe ejection before reporting success, leaving the workflow
  retryable when unmount or power-off fails.
- Rejected unknown gPhoto2 auto-detection output instead of silently treating unparsed cameras
  as absent.
- Physical Raspberry Pi and camera qualification is deferred.
