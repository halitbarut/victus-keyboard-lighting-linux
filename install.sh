#!/bin/sh
# Installs the victus_kbd kernel module (via DKMS when available), the lighting
# daemon and the GUI.  Usage: sudo ./install.sh [--user NAME] [--no-dkms]
set -e
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")"

PKG=victus-kbd
PKG_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' victus_kbd/__init__.py)
LIB_DIR=/usr/local/lib/victus-kbd
CONF_DIR=/etc/victus-kbd
USER_NAME="${SUDO_USER:-}"
USE_DKMS=auto

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME="$2"; shift 2 ;;
        --no-dkms) USE_DKMS=no; shift ;;
        -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root: sudo ./install.sh"
[ -n "$USER_NAME" ] || die "could not tell which desktop user will use the app; pass --user NAME"
id "$USER_NAME" >/dev/null 2>&1 || die "no such user: $USER_NAME"

# ---------------------------------------------------------------- checks
# Read os-release in a subshell: it defines VERSION, NAME, ... which must not leak here
DISTRO=$( (. /etc/os-release && echo "$ID $ID_LIKE") 2>/dev/null || true)

hint_packages() {
    case "$DISTRO" in
        *fedora*|*rhel*|*centos*)
            echo "  sudo dnf install python3-pyqt6 kernel-devel-\$(uname -r) gcc make dkms" ;;
        *ubuntu*|*debian*)
            echo "  sudo apt install python3-pyqt6 linux-headers-\$(uname -r) build-essential dkms" ;;
        *arch*)
            echo "  sudo pacman -S python-pyqt6 linux-headers base-devel dkms" ;;
        *suse*)
            echo "  sudo zypper install python3-PyQt6 kernel-devel gcc make dkms" ;;
        *)
            echo "  python3 + PyQt6 (Qt >= 6.4), kernel headers for $(uname -r), gcc, make (dkms recommended)" ;;
    esac
}

missing=""
command -v python3 >/dev/null || missing="$missing python3"
command -v make >/dev/null || missing="$missing make"
command -v gcc >/dev/null || missing="$missing gcc"
[ -d "/lib/modules/$(uname -r)/build" ] || missing="$missing kernel-headers"
if command -v python3 >/dev/null && ! python3 -c "
import sys
from PyQt6.QtCore import QT_VERSION
from PyQt6 import QtWidgets, QtNetwork
sys.exit(sys.version_info < (3, 8) or QT_VERSION < 0x060400)" 2>/dev/null; then
    missing="$missing PyQt6(Qt>=6.4)"
fi
if [ -n "$missing" ]; then
    echo "Missing:$missing" >&2
    echo "Install them with:" >&2
    hint_packages >&2
    exit 1
fi

vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo unknown)
product=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)
case "$vendor" in
    HP|Hewlett-Packard) ;;
    *) warn "this is not an HP laptop ($vendor $product); the module will most likely refuse to load" ;;
esac
case "$product" in
    *Victus*|*OMEN*|*Omen*) ;;
    *) warn "$product is not a Victus/Omen; continuing, but it may not have an RGB keyboard" ;;
esac

if command -v mokutil >/dev/null && mokutil --sb-state 2>/dev/null | grep -q "SecureBoot enabled"; then
    warn "Secure Boot is enabled: the module must be signed to load (see README, 'Secure Boot')"
fi

if [ "$USE_DKMS" = auto ]; then
    command -v dkms >/dev/null && USE_DKMS=yes || USE_DKMS=no
fi

# ---------------------------------------------------------------- module
say "Stopping the old service and module (if any)"
systemctl stop $PKG.service 2>/dev/null || true
systemctl disable --now victus-rainbow.service 2>/dev/null || true
rm -f /etc/systemd/system/victus-rainbow.service /usr/local/bin/victus-rainbow
modprobe -r victus_kbd 2>/dev/null || true

SRC="/usr/src/$PKG-$PKG_VERSION"

# Re-running the installer with an unchanged module skips DKMS entirely: on some
# distributions (Fedora) every DKMS transaction rebuilds all initramfs images.
dkms_current() {
    [ "$USE_DKMS" = yes ] &&
        cmp -s module/victus_kbd.c "$SRC/victus_kbd.c" &&
        cmp -s module/Makefile "$SRC/Makefile" &&
        dkms status "$PKG/$PKG_VERSION" -k "$(uname -r)" 2>/dev/null | grep -q installed
}

if dkms_current; then
    say "Kernel module $PKG $PKG_VERSION is already installed with DKMS"
else
    # Drop every earlier DKMS version and any manually installed copy
    if command -v dkms >/dev/null; then
        dkms status "$PKG" 2>/dev/null | sed -E "s#^${PKG}[/, ]+([^,:]+).*#\1#" | sort -u |
            while IFS= read -r v; do
                say "Removing DKMS version $v (this can take a minute)"
                dkms remove "$PKG/$v" --all >/dev/null 2>&1 || true
            done
    fi
    rm -rf "/var/lib/dkms/$PKG" /usr/src/"$PKG"-*
    find /lib/modules -path '*/extra/victus_kbd.ko*' -delete 2>/dev/null || true

    if [ "$USE_DKMS" = yes ]; then
        say "Installing the kernel module with DKMS ($PKG $PKG_VERSION, this can take a minute)"
        install -d "$SRC"
        install -m644 module/victus_kbd.c module/Makefile "$SRC/"
        sed "s/@VERSION@/$PKG_VERSION/" module/dkms.conf > "$SRC/dkms.conf"
        dkms install "$PKG/$PKG_VERSION" || die "DKMS build failed; see the log above"
    else
        say "Building the kernel module (no DKMS: re-run this script after kernel updates)"
        BUILD=$(mktemp -d)
        trap 'rm -rf "$BUILD"' EXIT
        cp module/victus_kbd.c module/Makefile "$BUILD/"
        make -C "$BUILD" >/dev/null || die "module build failed"
        install -Dm644 "$BUILD/victus_kbd.ko" "/lib/modules/$(uname -r)/extra/victus_kbd.ko"
        depmod -a
    fi
fi

say "Checking that the module loads"
if ! modprobe victus_kbd; then
    dmesg | grep -i victus_kbd | tail -5 >&2 || true
    die "the module did not load; this model may not be supported (see README, 'Troubleshooting')"
fi

# ---------------------------------------------------------------- app
say "Installing the app to $LIB_DIR"
rm -rf "$LIB_DIR"
install -d "$LIB_DIR/victus_kbd"
install -m644 victus_kbd/*.py "$LIB_DIR/victus_kbd/"

cat > /usr/local/bin/victus-kbd-daemon <<SH
#!/bin/sh
PYTHONPATH=$LIB_DIR exec python3 -m victus_kbd.daemon "\$@"
SH
cat > /usr/local/bin/victus-kbd <<SH
#!/bin/sh
PYTHONPATH=$LIB_DIR exec python3 -m victus_kbd.gui "\$@"
SH
chmod 755 /usr/local/bin/victus-kbd-daemon /usr/local/bin/victus-kbd

# The settings belong to the desktop user so the GUI can save without sudo
install -d -m755 "$CONF_DIR"
chown "$USER_NAME" "$CONF_DIR"
if [ ! -f "$CONF_DIR/config.json" ]; then
    # sudo keeps the user's LANG, so default profile names come out in their language
    PYTHONPATH=$LIB_DIR python3 -c \
        "from victus_kbd.effects import DEFAULTS, save_config; save_config(DEFAULTS)"
fi
chown "$USER_NAME" "$CONF_DIR/config.json"

install -Dm644 data/victus-kbd.service /etc/systemd/system/$PKG.service
install -Dm644 data/victus-kbd.desktop /usr/local/share/applications/victus-kbd.desktop
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/local/share/applications || true

say "Starting the service"
systemctl daemon-reload
systemctl enable $PKG.service >/dev/null
systemctl restart $PKG.service
sleep 1
systemctl is-active --quiet $PKG.service || die "the service did not start: journalctl -u $PKG"

echo
say "Done. Open 'Victus Keyboard Lighting' from the app menu, or run: victus-kbd"
echo "    If the app was already running, quit it from the tray icon and start it again."
