# Contributing

Thanks for helping! Bug reports, hardware reports, translations, docs and code are all welcome.
You can write issues and pull requests in English or Turkish.

## Reporting a model (works / doesn't work)

The most useful contribution is telling us whether your laptop works. Please
[open a hardware report](../../issues/new?template=hardware_report.md) and include:

```bash
cat /sys/class/dmi/id/{product_name,board_name,bios_version}
uname -r
sudo dmesg | grep victus_kbd
cat /sys/devices/platform/victus-kbd/brightness_raw   # if the module loaded
```

Also say how many lighting zones your keyboard has (one, four, per-key) and what you tried.

## Reporting a bug

Use the bug report template and include the output of:

```bash
systemctl status victus-kbd
journalctl -u victus-kbd -b --no-pager | tail -50
cat /etc/victus-kbd/config.json
```

and the terminal output of `victus-kbd` if the window misbehaves.

## Project layout

```
module/victus_kbd.c    kernel module: HP BIOS WMI → /sys/devices/platform/victus-kbd/
module/dkms.conf       DKMS template (@VERSION@ is filled in by install.sh)
victus_kbd/effects.py  effect math, schedule, battery rules, config load/save
victus_kbd/daemon.py   root service that applies the effect to sysfs
victus_kbd/gui.py      PyQt6 window, tray icon and command line
victus_kbd/i18n.py     translations (Turkish source strings → English)
data/                  systemd unit and .desktop file
tests/                 unittest suite (no hardware or root needed)
install.sh             installer (DKMS when available)
uninstall.sh           uninstaller
```

## Running from the source tree

Nothing has to be installed to work on the Python side. Point the app at a scratch
settings folder and the daemon at a fake sysfs folder:

```bash
mkdir -p /tmp/vk/etc /tmp/vk/sys && touch /tmp/vk/sys/color /tmp/vk/sys/brightness
export VICTUS_KBD_CONFIG_DIR=/tmp/vk/etc VICTUS_KBD_SYSFS=/tmp/vk/sys

python3 -m victus_kbd.daemon &        # writes colors to /tmp/vk/sys/color
python3 -m victus_kbd.gui             # the window
watch -n0.2 cat /tmp/vk/sys/color     # see what the keyboard would get
```

`VICTUS_KBD_LANG=en` or `VICTUS_KBD_LANG=tr` forces the UI language.

To try the real keyboard, stop the installed service first (`sudo systemctl stop victus-kbd`)
and run the daemon with `sudo` and without `VICTUS_KBD_SYSFS`.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The tests use a temporary settings folder, a fake clock and a fake keyboard, and run the GUI
with `QT_QPA_PLATFORM=offscreen`, so they need no hardware, root or display. Please add a
test with every fix or feature; CI runs the suite and builds the kernel module on each push.

## Kernel module

```bash
make -C module            # build
sudo insmod module/victus_kbd.ko
sudo rmmod victus_kbd
make -C module clean
```

Be careful with firmware calls: an unknown WMI command can change settings on some models.
Keep new calls behind a read (GET) check that proves the feature exists, the way `vk_init()`
probes the color before registering anything. Kernel code follows the kernel coding style
(`scripts/checkpatch.pl --no-tree -f module/victus_kbd.c`).

## Code style

- Python: standard library + PyQt6 only, PEP 8, 100-column lines, short comments that say *why*.
- Every user-visible string goes through `T("...")` with the Turkish text as the key; add the
  English translation to `EN` in `victus_kbd/i18n.py`. `tests/test_i18n.py` fails if one is missing.
- Shell scripts: POSIX `sh` (no bashisms), checked with `shellcheck`.

## Adding a language

1. Add a dict (for example `DE`) next to `EN` in `victus_kbd/i18n.py` with every key of `EN`.
2. Pick it in `detect_language()` and `T()`.
3. Add `Name[xx]=` / `Comment[xx]=` lines to `data/victus-kbd.desktop`.

## Pull requests

1. Fork, create a branch, make the change.
2. Run the tests (and `make -C module` if you touched the module).
3. Describe what you changed and on which laptop/distro you tested it.

## Releases

The version is in `victus_kbd/__init__.py` and `MODULE_VERSION` in `module/victus_kbd.c`;
keep them equal. `install.sh` uses it for the DKMS package version.

By contributing you agree that your work is released under the project's
[MIT license](LICENSE) (MIT or GPL-2.0 for the kernel module).
