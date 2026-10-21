import copy
import datetime as dt
import json
import os
import unittest

from tests import clean_config_dir
from victus_kbd.effects import (
    CONFIG_PATH, DEFAULTS, EFFECTS, compute_color, heartbeat_period, load_config, period,
    resolve, save_config, schedule_active,
)

FRI = dt.datetime(2026, 10, 23)  # a Friday


def at(day, h, m=0):
    return day.replace(hour=h, minute=m)


class ScheduleTest(unittest.TestCase):
    def sched(self, start, end, days=range(7)):
        return {"enabled": True, "start": start, "end": end, "days": list(days)}

    def test_disabled_is_always_on(self):
        self.assertTrue(schedule_active({"enabled": False, "start": "10:00", "end": "11:00"}, FRI))

    def test_same_day_range(self):
        s = self.sched("09:00", "17:00")
        self.assertFalse(schedule_active(s, at(FRI, 8, 59)))
        self.assertTrue(schedule_active(s, at(FRI, 9)))
        self.assertTrue(schedule_active(s, at(FRI, 16, 59)))
        self.assertFalse(schedule_active(s, at(FRI, 17)))

    def test_overnight_range(self):
        s = self.sched("19:00", "07:00")
        self.assertFalse(schedule_active(s, at(FRI, 17, 23)))  # the reported "stuck" case
        self.assertTrue(schedule_active(s, at(FRI, 19)))
        self.assertTrue(schedule_active(s, at(FRI, 23, 59)))
        self.assertTrue(schedule_active(s, at(FRI, 6, 59)))
        self.assertFalse(schedule_active(s, at(FRI, 7)))

    def test_overnight_belongs_to_start_day(self):
        s = self.sched("22:00", "02:00", days=[4])  # Friday night only
        sat = FRI + dt.timedelta(days=1)
        self.assertTrue(schedule_active(s, at(FRI, 23)))
        self.assertTrue(schedule_active(s, at(sat, 1)))
        self.assertFalse(schedule_active(s, at(FRI, 1)))   # Thursday night's part
        self.assertFalse(schedule_active(s, at(sat, 23)))

    def test_weekdays_and_empty_days(self):
        s = self.sched("00:00", "23:59", days=range(5))
        self.assertTrue(schedule_active(s, at(FRI, 12)))
        self.assertFalse(schedule_active(s, at(FRI + dt.timedelta(days=1), 12)))
        self.assertFalse(schedule_active(self.sched("09:00", "17:00", days=[]), at(FRI, 12)))

    def test_equal_start_end_means_all_day(self):
        self.assertTrue(schedule_active(self.sched("08:00", "08:00"), at(FRI, 3)))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULTS)

    def test_battery_modes(self):
        self.assertEqual(resolve(self.cfg, ac=False), (True, 100))
        self.cfg["battery"] = {"mode": "dim", "brightness": 30}
        self.assertEqual(resolve(self.cfg, ac=False), (True, 30))
        self.assertEqual(resolve(self.cfg, ac=True), (True, 100))
        self.cfg["battery"]["mode"] = "off"
        self.assertEqual(resolve(self.cfg, ac=False)[0], False)

    def test_disabled_and_zero_brightness(self):
        self.cfg["enabled"] = False
        self.assertFalse(resolve(self.cfg, ac=True)[0])
        self.cfg["enabled"] = True
        self.cfg["brightness"] = 0
        self.assertFalse(resolve(self.cfg, ac=True)[0])

    def test_schedule(self):
        self.cfg["schedule"] = {"enabled": True, "start": "19:00", "end": "07:00", "days": list(range(7))}
        self.assertFalse(resolve(self.cfg, now=at(FRI, 17), ac=True)[0])
        self.assertTrue(resolve(self.cfg, now=at(FRI, 20), ac=True)[0])


class ComputeTest(unittest.TestCase):
    def test_all_effects_stay_in_range(self):
        cfg = copy.deepcopy(DEFAULTS)
        for effect in EFFECTS:
            for speed in (1, 50, 100):
                cfg.update(effect=effect, speed=speed)
                for i in range(400):
                    rgb = compute_color(cfg, 1.76e9 + i * 0.037)
                    self.assertEqual(len(rgb), 3)
                    for c in rgb:
                        self.assertIsInstance(c, int)
                        self.assertTrue(0 <= c <= 255, (effect, rgb))

    def test_brightness_scales(self):
        cfg = dict(copy.deepcopy(DEFAULTS), effect="static", color="ff8000")
        self.assertEqual(compute_color(cfg, 0, 100), (255, 128, 0))
        self.assertEqual(compute_color(cfg, 0, 50), (128, 64, 0))
        self.assertEqual(compute_color(cfg, 0, 0), (0, 0, 0))

    def test_rainbow_covers_hues(self):
        cfg = dict(copy.deepcopy(DEFAULTS), effect="rainbow", speed=100)
        p = period(100)
        colors = {compute_color(cfg, p * k / 6) for k in range(6)}
        self.assertIn((255, 0, 0), colors)
        self.assertIn((0, 255, 0), colors)
        self.assertIn((0, 0, 255), colors)

    def test_cycle_hits_each_color(self):
        cfg = dict(copy.deepcopy(DEFAULTS), effect="cycle", speed=100, colors=["ff0000", "00ff00", "0000ff"])
        p = period(100)
        self.assertEqual(compute_color(cfg, 0), (255, 0, 0))
        self.assertEqual(compute_color(cfg, p / 3), (0, 255, 0))
        self.assertEqual(compute_color(cfg, 2 * p / 3), (0, 0, 255))
        cfg["colors"] = ["123456"]
        self.assertEqual(compute_color(cfg, 5), (0x12, 0x34, 0x56))

    def test_breathing_and_heartbeat_vary(self):
        for effect in ("breathing", "heartbeat", "fire"):
            cfg = dict(copy.deepcopy(DEFAULTS), effect=effect, color="ffffff", speed=50)
            span = period(50) if effect == "breathing" else 10.0
            values = {compute_color(cfg, span * i / 200)[0] for i in range(200)}
            self.assertGreater(max(values) - min(values), 100, effect)

    def test_speed_mappings(self):
        self.assertAlmostEqual(period(1), 120.0)
        self.assertAlmostEqual(period(100), 2.0)
        self.assertAlmostEqual(60 / heartbeat_period(1), 40.0)
        self.assertAlmostEqual(60 / heartbeat_period(100), 150.0)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        clean_config_dir()

    def write_raw(self, text):
        with open(CONFIG_PATH, "w") as f:
            f.write(text)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_config(), DEFAULTS)

    def test_roundtrip(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(effect="fire", color="abcdef", speed=12, brightness=34)
        cfg["schedule"]["days"] = [1, 3]
        cfg["profiles"] = {"X": {"effect": "static", "color": "010203", "colors": ["ffffff"],
                                 "speed": 5, "brightness": 6}}
        save_config(cfg)
        self.assertEqual(load_config(), cfg)
        self.assertFalse([f for f in os.listdir(os.path.dirname(CONFIG_PATH)) if f.startswith(".tmp")])

    def test_corrupt_json(self):
        self.write_raw("{not json")
        self.assertEqual(load_config(), DEFAULTS)
        self.write_raw("[1, 2]")
        self.assertEqual(load_config(), DEFAULTS)

    def test_invalid_values_are_fixed(self):
        self.write_raw(json.dumps({
            "effect": "lava", "color": "xyz", "colors": ["00ff00", 5, "nothex"],
            "speed": 500, "brightness": "high",
            "schedule": {"enabled": 1, "start": "25:00", "end": "7", "days": [1, 9, "x", 1]},
            "battery": {"mode": "maybe", "brightness": -4},
            "profiles": {"Ok": {"effect": "fire", "color": "FF0000"}, "Bad": 3, "": {}},
        }))
        c = load_config()
        self.assertEqual(c["effect"], DEFAULTS["effect"])
        self.assertEqual(c["color"], DEFAULTS["color"])
        self.assertEqual(c["colors"], ["00ff00"])
        self.assertEqual(c["speed"], 100)
        self.assertEqual(c["brightness"], DEFAULTS["brightness"])
        self.assertEqual(c["schedule"], {"enabled": True, "start": "18:00", "end": "07:00", "days": [1]})
        self.assertEqual(c["battery"], {"mode": "same", "brightness": 5})
        self.assertEqual(list(c["profiles"]), ["Ok"])
        self.assertEqual(c["profiles"]["Ok"]["color"], "ff0000")
        for effect in EFFECTS:  # must never crash on a sanitized config
            compute_color(dict(c, effect=effect), 123.4)


if __name__ == "__main__":
    unittest.main()
