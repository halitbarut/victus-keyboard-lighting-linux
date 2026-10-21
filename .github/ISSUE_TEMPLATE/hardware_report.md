---
name: Hardware report
about: Tell us whether your laptop model works
title: "[Hardware] <your model>"
labels: hardware
---

**Does it work?** yes / partly / no

**Model** (`cat /sys/class/dmi/id/product_name`):
**Board** (`cat /sys/class/dmi/id/board_name`):
**BIOS** (`cat /sys/class/dmi/id/bios_version`):
**Keyboard zones** (one / four / per-key):
**Distribution and kernel** (`uname -r`):
**Desktop environment:**
**Secure Boot** (`mokutil --sb-state`):

**`sudo dmesg | grep victus_kbd`:**
```
```

**`cat /sys/devices/platform/victus-kbd/brightness_raw`** (if the module loaded):
```
```

**Notes** (what works, what doesn't):
