# Installing Pi Scan 2

This document takes you from a release artifact to a running scanner. For the
operational detail behind the appliance install, including UDisks authorization
and the display-server situation, see `deploy/README.md`. For what changes when
you come from Pi Scan 1.5, see `MIGRATION.md`.

Physical qualification on a Raspberry Pi, on GPIO, and on real cameras has not
been performed. Keep the 1.5 appliance available on production scanners until it
has. `RELEASE_CHECKLIST.md` records that boundary.

## What you need

On the scanner:

- Raspberry Pi or comparable Linux host with an X11 desktop session. Wayland,
  which current Raspberry Pi OS releases and the Raspberry Pi 5 default to, is
  untested. Read the "Display server" section of `deploy/README.md` first.
- Python 3.13 with `venv` support. Earlier versions are refused by the
  installer.
- `chdkptp`. CHDK support is mandatory, because many supported Canon cameras
  depend on it.
- `udisks2`, providing `udisksctl`, and `lsblk` from util-linux 2.37 or later.
- The system libraries Kivy needs for its SDL2 window.
- `gphoto2` and `libgphoto2` only if you use DSLR or mirrorless cameras. That
  backend is optional, and it is consulted only when no CHDK camera answers.
- One USB stick or card reader for the scans. The appliance requires exactly one
  removable USB volume and will not touch a volume that carries the system.

On a development machine you need only Python 3.13; the simulator runs without
any camera, GPIO, or removable media.

## Which artifact to use

- **Source distribution** (`pi_scan-2.0.0.tar.gz`) for the scanner. It carries
  `deploy/` and `pyproject.toml`, which the installer needs in order to pull the
  `ui` and `hardware` extras.
- **Wheel** (`pi_scan-2.0.0-py3-none-any.whl`) for a machine that only needs the
  simulator, or for the in-field update path described below.
- **Git checkout** for development.

Build both artifacts from a checkout with:

    python -m build

## Installing on the scanner

Unpack the source distribution and run the installer as root. Its optional
argument is the account the scanner runs as:

    tar -xzf pi_scan-2.0.0.tar.gz
    cd pi_scan-2.0.0
    sudo sh deploy/install.sh

Omitted, it uses the account that invoked `sudo`, which on a current image is
the one you want. Raspberry Pi OS has created no default `pi` account since
April 2022: the first account is named when the card is written, through the
Imager, the setup wizard, or `userconf.txt`. Pass the name explicitly if you are
installing from a root shell, where `sudo` sets nothing to inherit. The account
must be the one that runs the desktop session, because the service reaches the
display through its home directory, and it may not be `root`.

The installer refuses to continue unless `python3.13`, `chdkptp`, `lsblk` and
`udisksctl` are all present and the named account exists. It then creates the
`pi-scan` system group, adds the account to `pi-scan`, `gpio` and `plugdev`,
builds a virtual environment at `/opt/pi-scan/venv`, installs the application
with its `ui` and `hardware` extras, and writes three files:

| Path | Purpose |
| --- | --- |
| `/etc/default/pi-scan` | display-server and input-hardware variables read by the unit |
| `/etc/systemd/system/pi-scan.service` | the appliance service |
| `/etc/udev/rules.d/99-pi-scan.rules` | camera access for the `pi-scan` group |

Set `PI_SCAN_INPUT` in `/etc/default/pi-scan` to `mouse` or `touch` for the
console's input hardware. Pi Scan 1.5 made that choice by shipping two disk
images, and the two remain alternatives rather than a combination: `touch` adds
the mtdev provider the official Raspberry Pi touchscreen needs.

Log out and back in so the supplementary groups apply, then confirm the scanner
account can reach the hardware without an authorization prompt:

    chdkptp -r -elist
    lsblk --json
    udisksctl mount --block-device /dev/YOUR_TEST_PARTITION

If the mount prompts for a password, fix the UDisks policy before going
further. `deploy/README.md` explains why a blanket passwordless rule is the
wrong answer. Then start the service:

    sudo systemctl enable --now pi-scan.service
    journalctl -u pi-scan.service -f

Selecting Done in the application exits successfully, which under
`Restart=on-failure` correctly does not restart the scanner. Stop the service
before changing cameras, deployment files, or Python packages.

## Installing for development or the simulator

    python -m pip install -e ".[dev,ui]"

That gives you the test suite, the linters, and both console commands. The
simulator needs no hardware:

    pi-scan-sim --output simulated-scans --pairs 2 --dng
    pi-scan-ui --output simulated-scans

Omit the `ui` extra if you only want `pi-scan-sim`; `pi-scan-ui` then reports
that Kivy is not installed rather than failing obscurely.

## Verifying an installation

Both console commands accept `--version` without starting Kivy or touching
hardware, which makes them a safe first check:

    /opt/pi-scan/venv/bin/pi-scan-sim --version
    /opt/pi-scan/venv/bin/pi-scan-ui --version

A simulator capture proves the imaging path end to end without a camera:

    /opt/pi-scan/venv/bin/pi-scan-sim --output /tmp/pi-scan-check --pairs 1 --dng

Pages `0000` and `0001` should each have a non-empty JPEG and DNG.

On the scanner itself, the first real run should reach the preparation screen
with both cameras named, and `debug/error.log` on the scan media should show the
same events in readable form.

## Updating an installed appliance

From a new source distribution, with the service stopped:

    sudo systemctl stop pi-scan.service
    sudo /opt/pi-scan/venv/bin/python -m pip install --force-reinstall \
        --no-deps /path/to/pi_scan-NEW.tar.gz
    sudo systemctl start pi-scan.service

The appliance also accepts an update carried on the scan media, as 1.5 did. Name
the file `pi-scan-update-<major>.<minor>.archive` and put exactly one wheel
inside it at the top level. The wheel must be this project and must carry the
version the file name advertises, or it is refused. An update whose version is
not newer than the running one is ignored. When a valid update is present it is
offered on the preparation screen, and installing it is always an explicit
operator action, because it runs code that arrived on removable media. A file
name is not a signature: treat the scan media as trusted, or do not use this
path.

## Uninstalling

    sudo systemctl disable --now pi-scan.service
    sudo rm -f /etc/systemd/system/pi-scan.service /etc/default/pi-scan \
        /etc/udev/rules.d/99-pi-scan.rules
    sudo rm -rf /opt/pi-scan
    sudo systemctl daemon-reload
    sudo udevadm control --reload-rules

The `pi-scan` group and the account's membership of `gpio` and `plugdev` are
left alone, since other software may rely on them. Remove them with `groupdel
pi-scan` and `gpasswd -d` if nothing else needs them. Nothing on the scan media
is touched.

## When something goes wrong

**`python3.13 is required`, or the same for `chdkptp`, `lsblk` or `udisksctl`.**
The installer preflight found a missing command. Install it and run the
installer again; it is safe to repeat.

**`unknown appliance user` or `invalid appliance user name`.** The argument to
`install.sh` must be an existing account whose name contains only letters,
digits, dot, hyphen and underscore. There has been no default `pi` account
since April 2022, so on a current image name the account explicitly, or let the
installer inherit it from `sudo`.

**`the appliance must not run as root`.** The service runs unprivileged and
locates the display through the account's home directory. Name the desktop
account instead.

**A reinstall appears to change nothing.** The installer force-reinstalls the
application, so a rebuilt tree replaces the code even when the version is
unchanged. Stop the service first, or systemd will still be running the code it
loaded at startup.

**`Insert one removable storage device`, or `Remove extra storage devices`.**
The appliance requires exactly one removable USB volume. A volume mounted at a
system path is never eligible, so the boot medium cannot be selected. Use
`--storage-timeout SECONDS` to make startup waiting finite instead of endless.

**`expected exactly two cameras, found N`.** Both cameras must be connected,
powered on, and out of any date and time prompt. If a camera shows that prompt,
unplug its USB cable, set the clock on the camera, and plug it back in.

**`reports no serial number`.** Configuration is keyed by camera serial, as in
1.5, so a camera that does not report one is refused rather than filed under an
identifier that would change with the USB socket.

**`insufficient storage`.** Free space fell below the reserve checked before
every capture. Change it with `--minimum-free-mib N`; the default is 256.

**`Kivy is not installed; install Pi Scan with the 'ui' extra`.** Reinstall with
`pip install ".[ui]"`, or use `pi-scan-sim`, which does not need it.

**The window appears but touch does nothing.** `PI_SCAN_INPUT` is probably
`mouse`. Set it to `touch` in `/etc/default/pi-scan` and restart the service.

**The service starts but no window appears.** Almost always the display server.
Check `echo "$XDG_SESSION_TYPE"` in the scanner account's session. If it says
`wayland`, the shipped X11 configuration does not apply; see the "Display
server" section of `deploy/README.md`.
