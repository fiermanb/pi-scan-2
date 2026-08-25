# Raspberry Pi appliance deployment

These files install the Python 3.13 rewrite independently of the legacy image builder.
They have not yet been qualified on the supported physical cameras, and they assume an
X11 session. See "Display server" below before deploying to a Wayland image.

Required operating-system packages include Python 3.13 with venv support, `chdkptp`,
`udisks2`, and the libraries required by Kivy. Install `libgphoto2`/`gphoto2` when using
non-CHDK cameras; that backend is optional and a missing executable produces a hardware
warning. Install from a local checkout:

    sudo sh deploy/install.sh

The appliance account defaults to whoever invoked `sudo`, since Raspberry Pi OS
has created no default `pi` account since April 2022. Pass a name to override
it. It must run the desktop session and must not be root. Re-running the
installer force-reinstalls the application, so a rebuilt tree replaces the code
on the scanner even when the version has not changed; stop the service first.

Set `PI_SCAN_INPUT` in `/etc/default/pi-scan` to `mouse` or `touch`. Pi Scan 1.5 made this
choice by shipping two disk images whose Kivy configurations differed, and described the mouse
variant as incompatible with touch; the same exclusivity applies here. Both settings name
`provider=hidinput` explicitly, because Kivy only adds it when it finds
`/opt/vc/include/bcm_host.h`, which 64-bit Raspberry Pi OS does not have.

Review `/etc/default/pi-scan`, log out and back in so supplementary groups apply, and verify
the scanner user can run all of these commands without an authorization prompt:

    chdkptp -r -elist
    gphoto2 --auto-detect
    lsblk --json
    udisksctl mount --block-device /dev/YOUR_TEST_PARTITION

UDisks authorization is distribution-specific. Prefer an active local desktop session and
the distribution's standard removable-media policy. Do not add a blanket passwordless
mount or power-off rule. The service passes `--no-user-interaction`, so it fails
safely rather than blocking on a prompt if policy does not authorize the scanner user.

After verification:

    sudo systemctl enable --now pi-scan.service
    journalctl -u pi-scan.service -f

The unit uses `Restart=on-failure`; selecting Done exits successfully and therefore does not
restart the scanner. Stop the service before changing cameras, deployment files, or Python
packages.

## Display server

`deploy/pi-scan.env` sets `DISPLAY=:0` and the unit sets `XAUTHORITY=%h/.Xauthority`. Both
are X11 settings, and they are the only display configuration this deployment has been
written against. Use an image whose desktop session is X11.

Current Raspberry Pi OS releases start a Wayland compositor, `labwc`, instead, and the
Raspberry Pi 5 ships with those releases. Under Wayland the variables above are meaningless:
a Wayland client is located by `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`, and Kivy reaches it
through SDL2, which may need `SDL_VIDEODRIVER=wayland`. Running the X11 configuration under
Xwayland is a further possibility. None of this has been run, so no Wayland or Raspberry Pi 5
support is claimed here. Treat the following as the shape of the work, not as instructions
that are known to succeed:

- Confirm which session the image starts, with `echo "$XDG_SESSION_TYPE"` in the scanner
  user's session.
- On a Wayland session, replace the X11 variables in `/etc/default/pi-scan` with
  `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` as that session exports them, and order the unit
  after the compositor rather than after `graphical.target`.
- Check that the pedal, full-resolution focus checking, and the fullscreen window behave as
  they do under X11 before putting the appliance into service.

Until that has been done on the hardware itself, deploy to an X11 image and keep the legacy
appliance available.
