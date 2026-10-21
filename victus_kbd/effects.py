"""Shared config handling and effect math for the daemon and the GUI."""
import colorsys
import copy
import datetime
import glob
import json
import math
import os
import tempfile

from victus_kbd.i18n import T

CONFIG_DIR = os.environ.get("VICTUS_KBD_CONFIG_DIR", "/etc/victus-kbd")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
# Unsaved settings the GUI is previewing; ignored once it stops being refreshed.
PREVIEW_PATH = os.path.join(CONFIG_DIR, "preview.json")
PREVIEW_TTL = 5.0

EFFECTS = ["static", "rainbow", "breathing", "cycle", "fire", "heartbeat"]
# Effects that use the single "color" setting
COLOR_EFFECTS = ("static", "breathing", "fire", "heartbeat")
# Keys that make up a lighting profile
PROFILE_KEYS = ("effect", "color", "colors", "speed", "brightness")

DEFAULTS = {
    "enabled": True,
    "effect": "rainbow",
    "color": "ff0000",
    "colors": ["ff0000", "00ff00", "0000ff"],
    "speed": 75,          # 1 (slowest) .. 100 (fastest)
    "brightness": 100,    # percent
    "schedule": {
        "enabled": False,
        "start": "18:00",
        "end": "07:00",
        "days": [0, 1, 2, 3, 4, 5, 6],  # Monday = 0
    },
    "battery": {
        "mode": "same",   # same | dim | off
        "brightness": 30,
    },
    "profiles": {
        T("Gökkuşağı"): {"effect": "rainbow", "color": "ff0000",
                      "colors": ["ff0000", "00ff00", "0000ff"], "speed": 75, "brightness": 100},
        T("Oyun"): {"effect": "static", "color": "ff0000",
                 "colors": ["ff0000", "00ff00", "0000ff"], "speed": 75, "brightness": 100},
        T("Gece"): {"effect": "static", "color": "ff6a00",
                 "colors": ["ff0000", "00ff00", "0000ff"], "speed": 75, "brightness": 20},
        T("Sakin"): {"effect": "breathing", "color": "0080ff",
                  "colors": ["ff0000", "00ff00", "0000ff"], "speed": 40, "brightness": 80},
        T("Şömine"): {"effect": "fire", "color": "ff4d00",
                   "colors": ["ff0000", "00ff00", "0000ff"], "speed": 60, "brightness": 100},
    },
}


def _merge(base, data):
    for k, v in data.items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict) and k != "profiles":
            _merge(base[k], v)
        else:
            base[k] = v


def _valid_hex(v):
    return isinstance(v, str) and len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v)


def _valid_hm(v):
    try:
        parse_hm(v)
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def _int_in(v, lo, hi, default):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return int(min(max(v, lo), hi))


def _sanitize_lighting(c, fallback):
    """Fix effect/color/speed/brightness fields in place (shared by config and profiles)."""
    if c.get("effect") not in EFFECTS:
        c["effect"] = fallback["effect"]
    c["color"] = c.get("color").lower() if _valid_hex(c.get("color")) else fallback["color"]
    colors = c.get("colors")
    colors = [x.lower() for x in colors if _valid_hex(x)] if isinstance(colors, list) else []
    c["colors"] = colors or list(fallback["colors"])
    c["speed"] = _int_in(c.get("speed"), 1, 100, fallback["speed"])
    c["brightness"] = _int_in(c.get("brightness"), 0, 100, fallback["brightness"])


def load_config(path=CONFIG_PATH):
    """Read the config, falling back to defaults for anything missing or invalid."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            _merge(cfg, data)
    except (OSError, ValueError):
        pass

    _sanitize_lighting(cfg, DEFAULTS)
    cfg["enabled"] = bool(cfg["enabled"])
    s, ds = cfg["schedule"], DEFAULTS["schedule"]
    s["enabled"] = bool(s.get("enabled"))
    for k in ("start", "end"):
        if not _valid_hm(s.get(k)):
            s[k] = ds[k]
    days = s.get("days")
    s["days"] = sorted({d for d in days if isinstance(d, int) and 0 <= d <= 6}) \
        if isinstance(days, list) else list(ds["days"])
    b = cfg["battery"]
    if b.get("mode") not in ("same", "dim", "off"):
        b["mode"] = DEFAULTS["battery"]["mode"]
    b["brightness"] = _int_in(b.get("brightness"), 5, 100, DEFAULTS["battery"]["brightness"])
    profiles = cfg["profiles"] if isinstance(cfg["profiles"], dict) else {}
    cfg["profiles"] = {}
    for name, prof in profiles.items():
        if isinstance(name, str) and name.strip() and isinstance(prof, dict):
            prof = {k: prof.get(k) for k in PROFILE_KEYS}
            _sanitize_lighting(prof, DEFAULTS)
            cfg["profiles"][name] = prof
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def period(speed):
    """Seconds for one effect cycle: speed 1 -> ~120 s, speed 100 -> 2 s."""
    speed = min(max(speed, 1), 100)
    return 2.0 + (100 - speed) * 118.0 / 99.0


def heartbeat_period(speed):
    """Seconds per beat: speed 1 -> 40 bpm, speed 100 -> 150 bpm."""
    speed = min(max(speed, 1), 100)
    return 60.0 / (40.0 + (speed - 1) * 110.0 / 99.0)


def parse_hex(s):
    v = int(s, 16)
    return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF


def parse_hm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def schedule_active(sched, now=None):
    """True when the lights should be on according to the schedule."""
    if not sched.get("enabled"):
        return True
    now = now or datetime.datetime.now()
    t = now.time()
    days = sched.get("days", DEFAULTS["schedule"]["days"])
    start, end = parse_hm(sched["start"]), parse_hm(sched["end"])
    if start == end:
        return now.weekday() in days
    if start < end:
        return start <= t < end and now.weekday() in days
    # Spans midnight: the early-morning part belongs to the previous day's range.
    if t >= start:
        return now.weekday() in days
    if t < end:
        return (now.weekday() - 1) % 7 in days
    return False


def on_ac_power():
    """True when plugged in (or when no AC adapter is reported)."""
    for supply in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(os.path.join(supply, "type")) as f:
                if f.read().strip() != "Mains":
                    continue
            with open(os.path.join(supply, "online")) as f:
                return f.read().strip() == "1"
        except OSError:
            continue
    return True


def resolve(cfg, now=None, ac=None):
    """Apply schedule and battery rules: returns (lights_on, effective brightness)."""
    if ac is None:
        ac = on_ac_power()
    bright = cfg["brightness"]
    if not ac:
        mode = cfg["battery"]["mode"]
        if mode == "off":
            return False, 0
        if mode == "dim":
            bright = min(bright, cfg["battery"]["brightness"])
    on = cfg["enabled"] and bright > 0 and schedule_active(cfg["schedule"], now)
    return on, bright


def _scale(rgb, v):
    return tuple(round(min(max(c * v, 0), 255)) for c in rgb)


def _pulse(x, center, width):
    """Smooth bump of height 1 around center (x and center in 0..1)."""
    d = abs(x - center)
    if d >= width:
        return 0.0
    return 0.5 + 0.5 * math.cos(math.pi * d / width)


def compute_color(cfg, t, brightness=None):
    """RGB tuple (0-255) for the configured effect at time t (seconds)."""
    p = period(cfg["speed"])
    if brightness is None:
        brightness = cfg["brightness"]
    bright = min(max(brightness, 0), 100) / 100.0
    effect = cfg["effect"]
    phase = (t / p) % 1.0

    if effect == "rainbow":
        r, g, b = colorsys.hsv_to_rgb(phase, 1.0, 1.0)
        rgb = (r * 255, g * 255, b * 255)
    elif effect == "breathing":
        # Smooth 0..1..0 wave, never fully dark so the change stays gentle.
        level = 0.05 + 0.95 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
        rgb = _scale(parse_hex(cfg["color"]), level)
    elif effect == "cycle":
        colors = [parse_hex(c) for c in cfg["colors"]]
        if len(colors) == 1:
            rgb = colors[0]
        else:
            pos = phase * len(colors)
            i = int(pos) % len(colors)
            f = pos - int(pos)
            a, b = colors[i], colors[(i + 1) % len(colors)]
            rgb = tuple(x + (y - x) * f for x, y in zip(a, b))
    elif effect == "fire":
        # Irregular flicker from overlapping sines; speed sets how lively it is.
        k = 2 * math.pi * 30.0 / p
        n = (math.sin(t * k * 0.31) + math.sin(t * k * 0.73 + 1.3)
             + math.sin(t * k * 1.37 + 2.1) + math.sin(t * k * 2.11 + 0.7)) / 4
        level = 0.65 + 0.35 * n
        base = parse_hex(cfg["color"])
        # Dimmer moments drift slightly towards red, like embers.
        rgb = _scale((base[0], base[1] * level, base[2] * level), level)
    elif effect == "heartbeat":
        # "lub-dub" then rest, at a realistic pulse rate.
        x = (t / heartbeat_period(cfg["speed"])) % 1.0
        level = 0.08 + 0.92 * max(_pulse(x, 0.08, 0.07), 0.7 * _pulse(x, 0.24, 0.07))
        rgb = _scale(parse_hex(cfg["color"]), level)
    else:
        rgb = parse_hex(cfg["color"])

    return _scale(rgb, bright)
