import copy
import os
import time
import unittest

from tests import clean_config_dir
from victus_kbd.daemon import BLACK, FADE, Daemon
from victus_kbd.effects import CONFIG_PATH, DEFAULTS, PREVIEW_PATH, PREVIEW_TTL, save_config


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
        self.d = Daemon(write=self.kb.write, clock=self.clock, ac=lambda: self.ac,
                        config_path=CONFIG_PATH, preview_path=PREVIEW_PATH)

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
        self.run_for(11, step=0.25)
        self.assertGreater(len(self.kb.writes), n)
        self.assertEqual(self.kb.color, "123456")

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


if __name__ == "__main__":
    unittest.main()
