import copy
import os
import time
import unittest

from tests import clean_config_dir
from victus_kbd.daemon import (
    BLACK, FADE, FPS, MAX_STEP, POWER_CHECK_EVERY, RESEND_EVERY, ConfigWatcher, Daemon,
    frame_interval,
)
from victus_kbd.effects import (
    CONFIG_DIR, CONFIG_PATH, DEFAULTS, EFFECTS, PREVIEW_PATH, PREVIEW_TTL, compute_color,
    save_config,
)


class FakeKeyboard:
    def __init__(self):
        self.color = None
        self.brightness = None
        self.writes = []
        self.fail = False

    def write(self, name, value):
        if self.fail:
            raise OSError("EIO")
        self.writes.append((name, value))
        setattr(self, name, value)

    def rgb(self):
        return tuple(int(self.color[i:i + 2], 16) for i in (0, 2, 4))


class mock_reload:
    """Records calls to Daemon.reload_if_changed while active."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.calls = []

    def __enter__(self):
        real = self.daemon.reload_if_changed
        self.daemon.reload_if_changed = lambda: (self.calls.append(1), real())
        return self.calls

    def __exit__(self, *exc):
        del self.daemon.reload_if_changed


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DaemonTest(unittest.TestCase):
    def setUp(self):
        clean_config_dir()
        self.kb = FakeKeyboard()
        self.clock = Clock()
        self.ac = True
        self.cfg = copy.deepcopy(DEFAULTS)
        self.save()
        self.suspended = 0.0  # seconds spent in (fake) suspend
        self.wall_offset = 0.0  # lets a test jump the wall clock forward
        self.d = self.make_daemon()

    def make_daemon(self, watcher=None):
        return Daemon(write=self.kb.write, clock=self.clock,
                      wall=lambda: time.time() + self.wall_offset,
                      boot=lambda: self.clock.t + self.suspended, ac=lambda: self.ac,
                      config_path=CONFIG_PATH, preview_path=PREVIEW_PATH, watcher=watcher)

    def save(self, **changes):
        self.cfg.update(changes)
        save_config(self.cfg)
        # Make sure the daemon sees a new mtime even within the same clock tick
        st = os.stat(CONFIG_PATH)
        os.utime(CONFIG_PATH, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def run_for(self, seconds, step=0.05):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.d.step()
            self.clock.t += step

    def test_fades_in_on_start(self):
        self.save(effect="static", color="ff0000")
        self.d.step()
        self.clock.t += 0.1
        self.d.step()
        self.assertEqual(self.kb.brightness, "1")
        self.assertNotIn(("brightness", "0"), self.kb.writes)
        self.assertLess(self.kb.rgb()[0], 255)  # fading in
        self.run_for(FADE + 0.2)
        self.assertEqual(self.kb.rgb(), (255, 0, 0))

    def test_turning_off_goes_black(self):
        """Regression: lights must not freeze on the last color when they turn off."""
        self.save(effect="rainbow", speed=100)
        self.run_for(1.5)
        self.assertNotEqual(self.kb.rgb(), BLACK)
        self.save(enabled=False)
        self.run_for(FADE + 0.3)
        self.assertEqual(self.kb.rgb(), BLACK)
        self.assertEqual(self.kb.brightness, "0")
        # Stays black afterwards, including the periodic re-send
        self.run_for(12, step=0.25)
        self.assertEqual(self.kb.rgb(), BLACK)

    def test_schedule_outside_range_is_black(self):
        now = time.localtime()
        start = f"{(now.tm_hour + 2) % 24:02d}:00"
        end = f"{(now.tm_hour + 3) % 24:02d}:00"
        self.save(effect="rainbow", schedule={"enabled": True, "start": start, "end": end,
                                              "days": list(range(7))})
        self.run_for(1.5)
        self.assertEqual(self.kb.rgb(), BLACK)
        self.assertTrue(all(v == "000000" for n, v in self.kb.writes if n == "color"))

    def test_battery_off_and_back(self):
        self.save(effect="static", color="00ff00", battery={"mode": "off", "brightness": 30})
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (0, 255, 0))
        self.ac = False
        self.run_for(6)  # power is re-checked every few seconds
        self.assertEqual(self.kb.rgb(), BLACK)
        self.ac = True
        self.run_for(6)
        self.assertEqual(self.kb.rgb(), (0, 255, 0))
        self.assertEqual(self.kb.brightness, "1")

    def test_battery_dim(self):
        self.save(effect="static", color="ffffff", battery={"mode": "dim", "brightness": 30})
        self.ac = False
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (76, 76, 76))

    def test_preview_overrides_and_expires(self):
        self.save(effect="static", color="ff0000")
        self.run_for(1)
        preview = dict(copy.deepcopy(self.cfg), color="0000ff")
        save_config(preview, PREVIEW_PATH)
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (0, 0, 255))
        old = time.time() - PREVIEW_TTL - 1
        os.utime(PREVIEW_PATH, (old, old))  # GUI stopped refreshing it
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (255, 0, 0))

    def test_static_does_not_spam_writes(self):
        self.save(effect="static", color="123456")
        self.run_for(2)
        n = len(self.kb.writes)
        self.run_for(5, step=0.25)
        self.assertEqual(len(self.kb.writes), n)

    def test_resend_after_interval(self):
        self.save(effect="static", color="123456")
        self.run_for(2)
        n = len(self.kb.writes)
        self.run_for(RESEND_EVERY + 1, step=0.25)
        self.assertGreater(len(self.kb.writes), n)
        self.assertEqual(self.kb.color, "123456")

    def test_resume_resends_immediately(self):
        self.save(effect="static", color="123456")
        self.run_for(2)
        n = len(self.kb.writes)
        self.run_for(1, step=0.25)
        self.assertEqual(len(self.kb.writes), n)   # nothing to do while steady
        self.suspended += 600                      # laptop slept for 10 minutes
        self.d.step()
        self.assertIn(("color", "123456"), self.kb.writes[n:])
        self.assertIn(("brightness", "1"), self.kb.writes[n:])

    def test_idle_sleeps_long_with_watcher(self):
        w = ConfigWatcher(CONFIG_DIR)
        self.addCleanup(w.close)
        self.d = self.make_daemon(watcher=w)
        self.save(effect="static", color="00ff00")
        self.run_for(1)
        delay = self.d.step()
        self.assertGreater(delay, 1.0)
        self.assertLessEqual(delay, POWER_CHECK_EVERY)
        # Without a watcher the config has to be polled quickly instead
        self.d = self.make_daemon()
        self.run_for(1)
        self.assertLessEqual(self.d.step(), 0.25)

    def test_watcher_reloads_on_change_only(self):
        w = ConfigWatcher(CONFIG_DIR)
        self.addCleanup(w.close)
        self.d = self.make_daemon(watcher=w)
        self.save(effect="static", color="ff0000")
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (255, 0, 0))
        with mock_reload(self.d) as calls:
            self.run_for(2)
        self.assertEqual(calls, [])                 # no file checks while nothing changes
        self.save(color="0000ff")
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (0, 0, 255))

    def test_watcher_drops_expired_preview(self):
        w = ConfigWatcher(CONFIG_DIR)
        self.addCleanup(w.close)
        self.d = self.make_daemon(watcher=w)
        self.save(effect="static", color="ff0000")
        self.run_for(1)
        save_config(dict(copy.deepcopy(self.cfg), color="00ff00"), PREVIEW_PATH)
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (0, 255, 0))
        # The GUI stops refreshing the preview: once it is stale the saved config wins
        self.wall_offset = PREVIEW_TTL + 1
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (255, 0, 0))

    def test_animated_delay_follows_effect_speed(self):
        self.save(effect="rainbow", speed=1)
        self.run_for(1)
        slow = self.d.step()
        self.save(effect="heartbeat", speed=50)
        self.run_for(1)
        self.assertAlmostEqual(self.d.step(), 1.0 / FPS)
        self.assertGreater(slow, 1.0 / FPS)

    def test_write_failure_recovers(self):
        self.save(effect="static", color="ff00ff")
        self.kb.fail = True
        self.assertEqual(self.d.step(), 2.0)
        self.kb.fail = False
        self.run_for(1)
        self.assertEqual(self.kb.rgb(), (255, 0, 255))

    def test_corrupt_config_does_not_crash(self):
        with open(CONFIG_PATH, "w") as f:
            f.write('{"effect": "cycle", "colors": ["zz"], "speed": "fast"}')
        self.run_for(1)
        self.assertIsNotNone(self.kb.color)

    def test_animated_effect_changes_color(self):
        self.save(effect="rainbow", speed=100)
        seen = set()
        for _ in range(40):
            self.d.step()
            seen.add(self.kb.color)
            self.clock.t += 0.05
            time.sleep(0.05)  # effect time follows the wall clock
        self.assertGreater(len(seen), 10)



class FrameIntervalTest(unittest.TestCase):
    def cfg(self, **kw):
        return dict(copy.deepcopy(DEFAULTS), **kw)

    def test_static_needs_no_frames(self):
        self.assertIsNone(frame_interval(self.cfg(effect="static"), 100))
        self.assertIsNone(frame_interval(self.cfg(effect="rainbow"), 0))

    def test_never_skips_visible_steps(self):
        """Lowering the frame rate must not make any effect jumpier than MAX_STEP per frame
        (unless even the full frame rate can't keep up, as before)."""
        for effect in EFFECTS:
            if effect == "static":
                continue
            for speed in (1, 30, 60, 100):
                for bright in (15, 100):
                    cfg = self.cfg(effect=effect, speed=speed, color="ff4d00",
                                   colors=["ff0000", "00ff00", "0000ff", "ffffff"])
                    iv = frame_interval(cfg, bright)
                    if iv == 1.0 / FPS:
                        continue
                    for i in range(int(20 / iv)):
                        a = compute_color(cfg, 1000 + i * iv, bright)
                        b = compute_color(cfg, 1000 + (i + 1) * iv, bright)
                        jump = max(abs(x - y) for x, y in zip(a, b))
                        self.assertLessEqual(jump, MAX_STEP, (effect, speed, bright, iv))

    def test_fast_effects_keep_full_rate(self):
        for effect in ("fire", "heartbeat"):
            self.assertEqual(frame_interval(self.cfg(effect=effect, speed=50), 100), 1.0 / FPS)

    def test_slow_effects_use_fewer_frames(self):
        self.assertGreaterEqual(frame_interval(self.cfg(effect="rainbow", speed=1), 100), 0.1)
        self.assertGreaterEqual(frame_interval(self.cfg(effect="breathing", speed=20), 100), 0.1)


class ConfigWatcherTest(unittest.TestCase):
    def setUp(self):
        clean_config_dir()
        self.w = ConfigWatcher(CONFIG_DIR)
        self.addCleanup(self.w.close)

    def test_reports_changes_once(self):
        self.assertFalse(self.w.drain())
        save_config(DEFAULTS)
        self.assertTrue(self.w.drain())
        self.assertFalse(self.w.drain())

    def test_wait_wakes_on_change(self):
        save_config(DEFAULTS)
        t = time.monotonic()
        self.w.wait(5)
        self.assertLess(time.monotonic() - t, 1)

    def test_wait_times_out(self):
        t = time.monotonic()
        self.w.wait(0.2)
        self.assertGreaterEqual(time.monotonic() - t, 0.15)

    def test_missing_directory(self):
        with self.assertRaises(OSError):
            ConfigWatcher(os.path.join(CONFIG_DIR, "does-not-exist"))


if __name__ == "__main__":
    unittest.main()
