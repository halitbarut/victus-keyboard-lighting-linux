---
name: Bug report
about: Something doesn't work as expected
title: ""
labels: bug
---

**What happened, and what did you expect?**

**Steps to reproduce**
1.
2.

**System**
- Model (`cat /sys/class/dmi/id/product_name`):
- Distribution, kernel (`uname -r`), desktop:
- victus-kbd version (`victus-kbd --version`):

**Logs**
```
systemctl status victus-kbd
journalctl -u victus-kbd -b --no-pager | tail -50
```

**Settings** (`cat /etc/victus-kbd/config.json`):
```json
```
