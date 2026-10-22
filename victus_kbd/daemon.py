"""Root daemon: applies the configured effect to the keyboard via sysfs."""
import ctypes
import ctypes.util
import math
import os
import select
import signal
import sys
import time

from victus_kbd.effects import (
    CONFIG_DIR, CONFIG_PATH, PREVIEW_PATH, PREVIEW_TTL, compute_color, heartbeat_period,
    load_config, on_ac_power, period, resolve,
)

SYSFS = os.environ.get("VICTUS_KBD_SYSFS", "/sys/devices/platform/victus-kbd")
FPS = 20                   # maximum frame rate
MAX_STEP = 2               # most color levels a channel may jump per frame (keeps fades smooth)
FADE = 0.6                 # seconds to fade in/out when the light turns on/off
RESEND_EVERY = 60.0        # safety re-send in case the firmware resets the color on its own
POWER_CHECK_EVERY = 5.0    # how often schedule and AC state are re-evaluated
POLL_INTERVAL = 0.25       # idle sleep when config changes can't be watched with inotify
BLACK = (0, 0, 0)


def sysfs_write(name, value):
    with open(os.path.join(SYSFS, name), "w") as f:
        f.write(value)


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def boottime():
    """Clock that keeps counting during suspend (unlike time.monotonic)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class ConfigWatcher:
    """Wakes the daemon when a file in the config directory changes (inotify)."""

    IN_ATTRIB, IN_CLOSE_WRITE, IN_MOVED_FROM = 0x004, 0x008, 0x040
    IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x080, 0x100, 0x200
    IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000

    def __init__(self, directory):
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.fd = libc.inotify_init1(self.IN_NONBLOCK | self.IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        mask = (self.IN_ATTRIB | self.IN_CLOSE_WRITE | self.IN_MOVED_FROM
                | self.IN_MOVED_TO | self.IN_CREATE | self.IN_DELETE)
        if libc.inotify_add_watch(self.fd, os.fsencode(directory), mask) < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, f"cannot watch {directory}")

    def drain(self):
        """True if anything changed since the last call."""
        changed = False
        while True:
            try:
                if not os.read(self.fd, 4096):
                    return changed
                changed = True
            except BlockingIOError:
                return changed

    def wait(self, timeout):
        select.select([self.fd], [], [], max(timeout, 0))

    def close(self):
        os.close(self.fd)


def frame_interval(cfg, bright):
    """Seconds between frames: the lowest frame rate at which no channel jumps more than
    MAX_STEP levels between two frames. None for effects that never change."""
    effect = cfg["effect"]
    if effect == "static" or bright <= 0:
        return None
    if effect == "heartbeat":
        window, periodic = heartbeat_period(cfg["speed"]), True
    elif effect == "fire":
        window, periodic = 10.0, False  # irregular: sample a long stretch
    else:
        window, periodic = period(cfg["speed"]), True
    # Sample at least twice per fastest frame so a frame interval is measured, not guessed
    n = max(400, math.ceil(window * FPS * 2))
    dt = window / n
    samples = [compute_color(cfg, i * dt, bright) for i in range(n)]
    for fps in range(1, FPS):
        k = math.ceil(1.0 / fps / dt)
        last = n if periodic else n - k
        if all(max(abs(a - b) for a, b in zip(samples[(i + k) % n], samples[i])) <= MAX_STEP
               for i in range(last)):
            return 1.0 / fps
    return 1.0 / FPS


class Daemon:
    """One step() per frame; clocks, AC state, the writer and the watcher are injectable."""

    def __init__(self, write=sysfs_write, clock=time.monotonic, wall=time.time,
                 boot=boottime, ac=on_ac_power, config_path=CONFIG_PATH,
                 preview_path=PREVIEW_PATH, watcher=None):
        self.write = write
        self.clock = clock
        self.wall = wall
        self.boot = boot
        self.ac = ac
        self.config_path = config_path
        self.preview_path = preview_path
        self.watcher = watcher
        self.cfg = None
        self.source = None
        self.preview_until = None   # wall time when the current preview file goes stale
        self.start = clock()
        self.last_step = self.start
        self.last_check = None
        self.last_resend = self.start
        self.sleep_gap = boot() - self.start   # grows by the length of every suspend
        self.on, self.bright = False, 0
        self.interval = None        # frame interval of the current effect
        self.interval_key = None
        self.level = 0.0            # fade level 0..1 actually shown on the keyboard
        self.sent = None            # last RGB written
        self.hw_on = None           # last value written to the brightness attribute

    def reload_if_changed(self):
        # A fresh preview file (GUI with unsaved changes) wins over the saved config.
        pm = mtime(self.preview_path)
        if pm is not None and self.wall() - pm < PREVIEW_TTL:
            src = (self.preview_path, pm)
            self.preview_until = pm + PREVIEW_TTL
        else:
            src = (self.config_path, mtime(self.config_path))
            self.preview_until = None
        if self.cfg is None or src != self.source:
            self.cfg = load_config(src[0])
            self.source = src
            self.last_check = None

    def needs_reload(self):
        if self.cfg is None or self.watcher is None:
            return True  # without inotify, check the files every frame
        if self.watcher.drain():
            return True
        return self.preview_until is not None and self.wall() >= self.preview_until

    def update_interval(self):
        key = (self.cfg["effect"], self.cfg["speed"], self.cfg["color"],
               tuple(self.cfg["colors"]), self.bright)
        if key != self.interval_key:
            self.interval = frame_interval(self.cfg, self.bright)
            self.interval_key = key

    def step(self):
        """Apply one frame; returns how long to sleep before the next one."""
        now = self.clock()
        dt = now - self.last_step
        self.last_step = now
        if self.needs_reload():
            self.reload_if_changed()

        # After suspend/resume the firmware may have reset the keyboard: send everything again.
        gap = self.boot() - now
        if gap - self.sleep_gap > 1.0:
            self.sent = self.hw_on = None
            self.last_check = None
        self.sleep_gap = gap

        if self.last_check is None or now - self.last_check >= POWER_CHECK_EVERY:
            self.on, self.bright = resolve(self.cfg, ac=self.ac())
            self.last_check = now
            self.update_interval()
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

        if self.level != target:
            return 1.0 / FPS  # fading
        if self.on and self.interval is not None:
            return self.interval
        # Nothing moves: sleep until the next scheduled check (config changes wake us earlier).
        idle = min(POWER_CHECK_EVERY - (now - self.last_check),
                   RESEND_EVERY - (now - self.last_resend))
        if self.preview_until is not None:
            idle = min(idle, self.preview_until - self.wall())
        if self.watcher is None:
            idle = min(idle, POLL_INTERVAL)
        return max(idle, 0.01)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        watcher = ConfigWatcher(CONFIG_DIR)
    except OSError as e:
        print(f"inotify unavailable ({e}); polling the config instead", file=sys.stderr, flush=True)
        watcher = None
    d = Daemon(watcher=watcher)
    while True:
        try:
            delay = d.step()
        except Exception as e:  # never die on a bad config; log and retry
            print(f"error: {e!r}", file=sys.stderr, flush=True)
            d.cfg = None
            delay = 2.0
        if watcher:
            watcher.wait(delay)
        else:
            time.sleep(delay)


if __name__ == "__main__":
    main()
