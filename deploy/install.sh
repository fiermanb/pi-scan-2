#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must be run as root" >&2
    exit 2
fi

pi_scan_user="${1:-${SUDO_USER:-pi}}"
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
install_root=/opt/pi-scan

case "$pi_scan_user" in
    ""|*[!A-Za-z0-9_.-]*)
        echo "invalid appliance user name: $pi_scan_user" >&2
        exit 2
        ;;
esac

# The unit runs the appliance unprivileged and reaches the desktop session
# through that account's home directory, so root is never the right answer.
if [ "$pi_scan_user" = "root" ]; then
    echo "the appliance must not run as root" >&2
    echo "pass the account that runs the desktop, as in: sh deploy/install.sh scanner" >&2
    exit 2
fi

for required_command in python3.13 chdkptp lsblk udisksctl; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "$required_command is required" >&2
        exit 2
    fi
done
if ! id "$pi_scan_user" >/dev/null 2>&1; then
    echo "unknown appliance user: $pi_scan_user" >&2
    echo "Raspberry Pi OS has created no default 'pi' account since April 2022." >&2
    echo "Pass the account that runs the desktop session." >&2
    exit 2
fi

getent group pi-scan >/dev/null 2>&1 || groupadd --system pi-scan
usermod -a -G pi-scan,gpio,plugdev "$pi_scan_user"

install -d -m 0755 "$install_root"
python3.13 -m venv "$install_root/venv"
"$install_root/venv/bin/python" -m pip install --upgrade pip
"$install_root/venv/bin/python" -m pip install --upgrade "${repo_root}[ui,hardware]"
# Reinstall the application itself even when its version has not changed, so
# re-running the installer with a rebuilt tree actually replaces the code.
"$install_root/venv/bin/python" -m pip install --force-reinstall --no-deps "$repo_root"

install -m 0644 "$repo_root/deploy/pi-scan.env" /etc/default/pi-scan
sed "s/@PI_SCAN_USER@/$pi_scan_user/g" "$repo_root/deploy/pi-scan.service" \
    > /etc/systemd/system/pi-scan.service
chmod 0644 /etc/systemd/system/pi-scan.service
install -m 0644 "$repo_root/deploy/99-pi-scan.rules" \
    /etc/udev/rules.d/99-pi-scan.rules

systemctl daemon-reload
udevadm control --reload-rules

echo "Installed Pi Scan for user $pi_scan_user."
echo "Review /etc/default/pi-scan and deploy/README.md."
echo "Then enable it with: systemctl enable --now pi-scan.service"
