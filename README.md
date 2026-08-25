# Pi Scan 2 Overview

Pi Scan is a camera controller for dual-camera book scanners. It locates two cameras, maintains their odd and even page assignments, captures both pages simultaneously, and writes the results to removable storage in reading order. It was originally made for the Archivist book scanner from the DIY Book Scanner community, but nothing in the software requires that particular frame.

Pi Scan 2 is a rewrite of the original appliance. The old program was tied to Python 2 and an aging Raspberry Pi image. This version keeps the scanning workflow and the useful parts of the old configuration format, but runs as an ordinary Python application that can be installed and updated without rebuilding an operating-system image.

Pi Scan is a machine controller rather than a general camera application. It expects exactly two cameras and one removable scan disk, and it assumes the cameras are mounted over the left and right pages of an open book. Within that scope there are correspondingly fewer decisions to make while scanning.

## Requirements

For a working scanner you need:

* A Raspberry Pi or similar Linux computer with an X11 desktop session
* Two supported cameras and their USB cables
* `chdkptp`, which the installer requires whichever cameras are used, and CHDK on each
  Canon compact camera
* A USB stick, USB hard disk, or card reader for the scanned pages
* A screen and at least one input device
* Python with virtual-environment support
* `udisks2` and `lsblk` for finding, mounting, and ejecting the scan disk

Pi Scan 2 requires Python 3.13. The installer looks for `python3.13` specifically, so earlier versions will not install. The package metadata accepts later versions, but they are neither tested nor listed as supported. [INSTALL.md](INSTALL.md) lists the exact software this release needs.

CHDK support is an essential part of the hardware application. Pi Scan always looks for CHDK cameras first. `gphoto2` is only needed for DSLR and mirrorless cameras, and is tried only when no CHDK camera answers. A session does not mix CHDK and gPhoto2 cameras.

## Input Device Options

Avoid a USB hub if at all possible. In the original Pi Scan a substantial share of reported problems turned out to be hub-related, and went away once the devices were connected to the Raspberry Pi directly.

### Touch Screen or Mouse

The graphical interface can be operated by touch or with a mouse. As in Pi Scan 1.5, the two
are alternatives chosen once for the console rather than at runtime: a mouse and any HDMI
screen, or the official Raspberry Pi touchscreen. Set `PI_SCAN_INPUT` in
`/etc/default/pi-scan` to `mouse` or `touch`, or pass `--input` when running the command
directly. The touch setting adds the mtdev provider that panel needs.

### Keyboard

The scanner can be operated entirely from a keyboard. Enter performs the main action shown on the current screen. Tab swaps the odd and even cameras before focus is locked. Use `+`, `-`, and `0` to enlarge, reduce, and reset the preview; pan it with the arrow keys or WASD.

During scanning, Space, B, or C captures a page pair. R rescans the previous pair. X starts recovery after a camera failure.

The numeric keypad behavior from Pi Scan 1.5 is retained. Its meaning depends on the screen:

* `1` prepares cameras, focuses them, or starts recovery
* `2` installs an offered update, saves a CHDK log, or turns off the cameras
* `3` focuses, rescans, or saves a CHDK log
* `5` swaps camera sides or finishes the book

No number key takes a photograph, so an accidental keypress cannot consume the next two page numbers.

### USB Foot Pedal

Most inexpensive USB pedals present themselves as keyboards. Configure the pedal to send Space, B, or C and it will trigger a capture while the scanning screen is open.

### Industrial Foot Pedal, Button, or Microswitch

A switch between GPIO21 and ground can be used as a capture pedal. Pi Scan configures that pin as an active-low input. The pedal takes photographs only on the scanning screen. It does nothing while zoom, shutter or focus is being set, and nothing while a capture failure is displayed, so acknowledging that failure cannot consume a page pair.

The application continues to accept touch and keyboard input if GPIO cannot be opened, and shows a warning instead of refusing to start.

## Cameras

### CHDK Cameras

CHDK must already be installed on each camera and must boot when the camera is turned on. Install the complete CHDK build made for the camera's exact Canon firmware version. A build for a model with a similar name, or even for a different firmware revision of the same model, is not a substitute.

Pi Scan communicates with CHDK through the `chdkptp` command-line client. Each camera must:

* appear in `chdkptp -r -elist` with a stable serial number
* support remote Lua commands
* report its optical zoom steps
* support autofocus locking
* return a JPEG through CHDK remote capture

Configuration is stored by serial number, not by USB socket. This lets the cameras be unplugged without losing their odd/even assignment. A camera reporting `s=nil` is refused because a bus address would change whenever it was reconnected.

CHDK supports many Canon cameras, but not every CHDK port necessarily provides every operation Pi Scan needs. Before relying on a new camera model, test the commands above with both cameras connected and make several complete page captures. A CHDK splash screen proves that CHDK booted; it does not by itself prove remote-capture compatibility.

### DSLR and Mirrorless Cameras

When no CHDK camera is found, Pi Scan can use two cameras reported by `gphoto2`. Focus, optical zoom, and most camera setup remain under the control of the camera itself. Set both cameras up before starting and remember that some settings, particularly zoom and manual focus, may be lost when a camera is powered off.

The gPhoto2 backend is optional; install it only when it is needed. Use `--no-gphoto` when diagnosing a CHDK scanner, so that an unrelated PTP entry cannot obscure the fault being investigated.

## Installation

Pi Scan 2 is distributed as a Python package rather than a complete Raspberry Pi disk image. This is less convenient than writing a single image, but the scanner is no longer fixed to the operating system that was current when the image was built.

The complete scanner installation is described in [INSTALL.md](INSTALL.md). From an unpacked source release or Git checkout:

```sh
sudo sh deploy/install.sh
```

The installer creates an isolated environment under `/opt/pi-scan`, installs a systemd service, adds USB-camera access rules, and prepares the scanner account for GPIO and removable-media access. It refuses to run the application as root.

The service expects an X11 desktop session belonging to the scanner account. Read [deploy/README.md](deploy/README.md) before enabling it.

For development, or to try the interface without cameras:

```sh
python -m pip install -e ".[dev,ui]"
pi-scan-sim --output simulated-scans --pairs 2 --dng
pi-scan-ui --output simulated-scans
```

The simulator creates ordinary page files and exercises the same session and publication logic used by the hardware application. It does not demonstrate that USB, GPIO, Kivy, or UDisks work on a particular Raspberry Pi.

## Preparing the Cameras and Scan Disk

Lock each CHDK camera's SD card if that is how its boot method starts CHDK. Turn the cameras on and wait for the CHDK splash screen. If a camera asks for the date or time, disconnect USB, answer the question on the camera, and reconnect it afterwards.

Confirm both cameras before starting:

```sh
chdkptp -r -elist
```

There should be two entries, each with a different non-`nil` serial number.

Put the scan disk in its reader before beginning. Pi Scan accepts exactly one removable USB filesystem and will not select the boot disk or a volume mounted at a system location. It creates these items on the scan disk:

* `images/` for numbered JPEG and optional RAW files
* `pi-scan.conf` for camera sides, zoom, and shutter choices
* `debug/error.log` for a readable error history
* `debug/events.jsonl` for detailed machine-readable events
* `images/.pi-scan-previews/` for the preview and focus-inspection images

Existing numbered images are left alone. A new session continues with the next even page number, so a book can be scanned over more than one session as long as its files remain on the disk.

## Usage

Start the installed service with:

```sh
sudo systemctl enable --now pi-scan.service
```

For a temporary hardware run from a development checkout, use:

```sh
python -m pip install -e ".[ui,hardware]"
pi-scan-ui --hardware
```

Pi Scan waits for the scan disk, verifies that it can write to it, and then searches for the cameras. Both cameras must be present before scanning begins.

### Scanning Steps

1. Insert the scan disk and turn on both cameras. Wait for CHDK to finish booting before connecting them if that is what their boot method requires.
2. Start Pi Scan. Check that it shows the expected scan disk and exactly two cameras.
3. Assign the cameras to the odd and even pages. If they are reversed, swap them before focusing. The assignments are saved by camera serial number.
4. Adjust zoom and shutter speed if necessary. Test photographs do not consume page numbers, so this is the right time to experiment.
5. Put two pages with plenty of printed detail against the platen and focus the cameras. Pi Scan autofocuses once and then locks focus for the scanning session.
6. Enlarge and pan the previews to inspect focus. Beyond 1:1 magnification the application shows a window cut from the original pixels rather than enlarging a reduced preview.
7. Put the book in position and press the pedal, Space, B, or C. Both cameras capture at once. Pi Scan publishes neither page unless both JPEGs are valid.
8. Turn the page while the images are being processed. Check the first few pairs carefully and keep checking at intervals, because a camera that has shifted is otherwise not discovered until the book is finished.
9. Use Rescan if the previous pair is blurred, obstructed, or otherwise wrong. The old pair is kept unless its replacement is completely written.
10. Select Finish when the book is done. Pi Scan waits for outstanding writes, synchronizes the disk, and ejects it. Do not remove the scan disk until the application says it is safe.

Before every capture Pi Scan keeps a free-space reserve of 256 MiB. The hardware command's `--minimum-free-mib` option changes that value when a different reserve is appropriate. `--storage-timeout` makes the wait for a scan disk finite instead of indefinite, and `--no-gpio` disables the GPIO pedal when no switch is wired.

### After Capture

Pi Scan controls capture only. The scan disk will contain alternating odd and even JPEG files named `0000.jpg`, `0001.jpg`, and so on. Cropping, deskewing, page cleanup, OCR, and assembly into PDF or another format are separate post-processing jobs. ScanTailor and similar programs can perform much of that work.

## Troubleshooting

Pi Scan records both a readable error history and detailed machine-readable events on the scan disk. Start with `debug/error.log`; use `debug/events.jsonl` when that is not enough.

### A Camera Is Missing

Run `chdkptp -r -elist` as the same account that runs Pi Scan. Check that both cameras are powered on, CHDK has booted, and neither camera is waiting for its date and time to be set. Desktop photo importers can claim a PTP camera before `chdkptp` reaches it; close or disable them.

If the application says a camera has no serial number, inspect the `s=` field from `chdkptp`. Pi Scan cannot safely remember a camera by a temporary USB bus address.

### Capture Failure

If either camera returns an empty or invalid photograph, neither half of the pair is added to the book. Acknowledge the error, recover the cameras if requested, refocus, and try the pair again. Repeated failures usually call for power-cycling the affected camera and checking its USB cable.

CHDK cameras sound their failure tone when possible. The recovery screen can also save the camera ROM log under `debug/` on the scan disk.

### The Scan Disk Is Missing

Only one removable USB filesystem may be attached. Remove spare card readers and USB disks. Pi Scan will not use a volume that contains the running system, even if Linux labels that volume removable.

If mounting asks for an administrator password, the scanner account does not have the required UDisks authorization. Do not solve this by running Pi Scan as root. Follow the UDisks notes in [deploy/README.md](deploy/README.md).

### Finish Cannot Eject the Disk

Wait a moment and try Finish again. Large writes may still be reaching the device. Pi Scan retries normal ejection before attempting a forced unmount. If that also fails, the interface stays open rather than reporting that removal is safe.

### The Service Runs but No Window Appears

Check the service log:

```sh
journalctl -u pi-scan.service -f
```

Then check the desktop session with `echo $XDG_SESSION_TYPE`. The service is configured for X11, so any other result means the deployment does not match the session; see [deploy/README.md](deploy/README.md).

### Pi Scan Stops Unexpectedly

Keep the scan disk inserted and copy `debug/error.log` and `debug/events.jsonl` before starting over. Existing published page pairs remain intact. Temporary or recovery files are also retained when deleting them would risk losing the only copy of an image.

## Updating

An update can be installed normally with `pip`, as described in [INSTALL.md](INSTALL.md). Pi Scan can also offer an update wheel placed on the scan disk in a file named `pi-scan-update-<major>.<minor>.archive`. The archive must contain exactly one Pi Scan wheel whose version agrees with the archive name.

Installing an update from removable media runs code from that media. Pi Scan therefore never installs it silently. Only accept an update from a source you trust.

## Version Notes

### 2.0.0

* Rewritten for modern Python while retaining the Pi Scan 1.5 scanning workflow
* Replaced the embedded Python/Lua camera bridge with supervised `chdkptp` commands
* Preserved serial-based camera configuration, zoom and shutter choices, page naming, keypad controls, focus locking, failure tones, ROM logs, and removable-media updates
* Added transactional paired capture and rescan so incomplete page pairs are not published
* Added a simulator, automated tests, structured diagnostics, packaging, and deployment files

For detailed changes from the Python 2 appliance, see [MIGRATION.md](MIGRATION.md) and [CHANGELOG.md](CHANGELOG.md).
