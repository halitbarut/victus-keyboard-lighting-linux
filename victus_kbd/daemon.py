"""Root daemon: applies the configured effect to the keyboard via sysfs."""
import os
import signal
import sys
import time

from victus_kbd.effects import (
    CONFIG_PATH, PREVIEW_PATH, PREVIEW_TTL, compute_color, load_config, on_ac_power, resolve,
)

SYSFS = os.environ.get("VICTUS_KBD_SYSFS", "/sys/devices/platform/victus-kbd")
FPS = 20
FADE = 0.6                 # seconds to fade in/out when the light turns on/off
RESEND_EVERY = 10.0        # re-send state so it survives suspend/resume or firmware resets
POWER_CHECK_EVERY = 5.0    # how often schedule and AC state are re-evaluated
BLACK = (0, 0, 0)


def sysfs_write(name, value):
    with open(os.path.join(SYSFS, name), "w") as f:
        f.write(value)


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


class Daemon:
    """One step() per frame; time, AC state and the writer are injectable for tests."""

    def __init__(self, write=sysfs_write, clock=time.monotonic, wall=time.time,
                 ac=on_ac_power, config_path=CONFIG_PATH, preview_path=PREVIEW_PATH):
        self.write = write
        self.clock = clock
        self.wall = wall
        self.ac = ac
        self.config_path = config_path
        self.preview_path = preview_path
        self.cfg = None
        self.source = None
        self.start = clock()
        self.last_step = self.start
        self.last_check = None
        self.last_resend = self.start
        self.on, self.bright = False, 0
        self.level = 0.0          # fade level 0..1 actually shown on the keyboard
        self.sent = None          # last RGB written
        self.hw_on = None         # last value written to the brightness attribute

    def reload_if_changed(self):
        # A fresh preview file (GUI with unsaved changes) wins over the saved config.
        pm = mtime(self.preview_path)
        if pm is not None and self.wall() - pm < PREVIEW_TTL:
            src = (self.preview_path, pm)
        else:
            src = (self.config_path, mtime(self.config_path))
        if self.cfg is None or src != self.source:
            self.cfg = load_config(src[0])
            self.source = src
            self.last_check = None

    def step(self):
        """Apply one frame; returns how long to sleep before the next one."""
        now = self.clock()
        dt = now - self.last_step
        self.last_step = now
        self.reload_if_changed()

        if self.last_check is None or now - self.last_check >= POWER_CHECK_EVERY:
            self.on, self.bright = resolve(self.cfg, ac=self.ac())
            self.last_check = now
        if now - self.last_resend >= RESEND_EVERY:
            self.sent = self.hw_on = None
            self.last_resend = now

        target = 1.0 if self.on else 0.0
        if self.level < target:
            self.level = min(target, self.level + dt / FADE)
        elif self.level > target:
            self.level = max(target, self.level - dt / FADE)

        if self.level > 0:
            # Wall-clock time is the shared time base, so the GUI preview matches.
            rgb = compute_color(self.cfg, self.wall(), self.bright)
            rgb = tuple(round(c * self.level) for c in rgb)
        else:
            rgb = BLACK

        try:
            # Some firmware ignores the brightness switch, so "off" is also sent as black.
            if self.on and self.hw_on is not True:
                self.write("brightness", "1")
                self.hw_on = True
            if rgb != self.sent:
                self.write("color", "%02x%02x%02x" % rgb)
                self.sent = rgb
            if self.level == 0 and not self.on and self.hw_on is not False:
                self.hw_on = False
                try:
                    self.write("brightness", "0")
                except OSError:
                    pass  # best effort; the keyboard is already black
        except OSError as e:
            print(f"sysfs write failed: {e}", file=sys.stderr, flush=True)
            self.sent = self.hw_on = None
            return 2.0

        animating = self.level != target or (self.on and self.cfg["effect"] != "static")
        return 1.0 / FPS if animating else 0.25


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    d = Daemon()
    while True:
        try:
            delay = d.step()
        except Exception as e:  # never die on a bad config; log and retry
            print(f"error: {e!r}", file=sys.stderr, flush=True)
            d.cfg = None
            delay = 2.0
        time.sleep(delay)


if __name__ == "__main__":
    main()
