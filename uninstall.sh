#!/bin/sh
# Removes everything install.sh installed.  Usage: sudo ./uninstall.sh [--purge]
#   --purge  also delete the settings in /etc/victus-kbd
set -e
PKG=victus-kbd
PURGE=no
[ "$1" = "--purge" ] && PURGE=yes

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

systemctl disable --now $PKG.service 2>/dev/null || true
rm -f /etc/systemd/system/$PKG.service
systemctl daemon-reload

modprobe -r victus_kbd 2>/dev/null || true
if command -v dkms >/dev/null; then
    dkms status "$PKG" 2>/dev/null | sed -E "s#^${PKG}[/, ]+([^,:]+).*#\1#" | sort -u |
        while IFS= read -r v; do
            dkms remove "$PKG/$v" --all >/dev/null 2>&1 || true
        done
fi
rm -rf "/var/lib/dkms/$PKG" /usr/src/"$PKG"-*
find /lib/modules -path '*/extra/victus_kbd.ko*' -delete 2>/dev/null || true
depmod -a

rm -rf /usr/local/lib/victus-kbd
rm -f /usr/local/bin/victus-kbd /usr/local/bin/victus-kbd-daemon
rm -f /usr/local/share/applications/victus-kbd.desktop
if [ "$PURGE" = yes ]; then
    rm -rf /etc/victus-kbd
fi

echo "victus-kbd removed."
[ "$PURGE" = yes ] || echo "Settings kept in /etc/victus-kbd (use --purge to delete them)."
echo "Per-user files you may also delete: ~/.config/autostart/victus-kbd.desktop ~/.config/victus-kbd/"
