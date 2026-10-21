"""PyQt6 control panel (and tray icon / CLI) for the Victus keyboard lighting daemon."""
import argparse
import copy
import os
import subprocess
import sys
import time

from PyQt6.QtCore import QSettings, QSize, QTime, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QSlider, QSystemTrayIcon, QTabWidget,
    QTimeEdit, QVBoxLayout, QWidget,
)

from victus_kbd import __version__
from victus_kbd.i18n import T
from victus_kbd.effects import (
    COLOR_EFFECTS, CONFIG_PATH, DEFAULTS, PREVIEW_PATH, PROFILE_KEYS, compute_color,
    heartbeat_period, load_config, on_ac_power, period, resolve, save_config, schedule_active,
)

EFFECT_NAMES = {
    "static": T("Sabit renk"),
    "rainbow": T("Gökkuşağı"),
    "breathing": T("Nefes"),
    "cycle": T("Renk geçişi"),
    "fire": T("Ateş"),
    "heartbeat": T("Kalp atışı"),
}

BATTERY_MODES = {
    "same": T("Aynı kalsın"),
    "dim": T("Parlaklığı düşür"),
    "off": T("Işığı kapat"),
}

DAY_NAMES = [T("Pzt"), T("Sal"), T("Çar"), T("Per"), T("Cum"), T("Cmt"), T("Paz")]

PRESETS = ["ff0000", "ff6a00", "ffd000", "00ff00", "00ffd0",
           "0080ff", "0000ff", "8000ff", "ff00c8", "ffffff"]

CUSTOM_PROFILE = T("— Özel —")
AUTOSTART_PATH = os.path.expanduser("~/.config/autostart/victus-kbd.desktop")
SOCKET_NAME = f"victus-kbd-{os.getuid()}"


def swatch_style(hexcolor, size=28):
    return (f"background:#{hexcolor}; border:1px solid palette(mid);"
            f"border-radius:{size // 2}px; min-width:{size}px; min-height:{size}px;"
            f"max-width:{size}px; max-height:{size}px;")


def color_icon(hexcolor):
    pm = QPixmap(30, 30)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(0, 0, 0, 90))
    p.setBrush(QColor("#" + hexcolor))
    p.drawRoundedRect(1, 1, 28, 28, 6, 6)
    p.end()
    return QIcon(pm)


def dot_icon(rgb):
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(0, 0, 0, 120))
    p.setBrush(QColor(*rgb) if rgb else QColor(70, 70, 70))
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pm)


def matching_profile(cfg):
    for name, prof in cfg["profiles"].items():
        if all(prof.get(k) == cfg[k] for k in PROFILE_KEYS):
            return name
    return None


def remove_preview():
    try:
        os.remove(PREVIEW_PATH)
    except FileNotFoundError:
        pass


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("victus-kbd", "gui")
        self.saved = load_config()
        self.cfg = copy.deepcopy(self.saved)
        self.cfg_mtime = self.config_mtime()
        self.ac = on_ac_power()
        self.service_state = "active"
        self.loading = False
        self.tray = None

        self.setWindowIcon(QIcon.fromTheme("input-keyboard"))
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)

        # ----- header: live preview, power, profiles -----
        self.preview = QFrame()
        self.preview.setFixedHeight(64)
        pv = QHBoxLayout(self.preview)
        self.preview_lbl = QLabel()
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setStyleSheet("color:#bbb; background:transparent; font-weight:bold;")
        pv.addWidget(self.preview_lbl)
        root.addWidget(self.preview)

        top = QHBoxLayout()
        self.power = QPushButton()
        self.power.setCheckable(True)
        self.power.setMinimumWidth(110)
        self.power.toggled.connect(self.changed)
        top.addWidget(self.power)
        top.addSpacing(12)
        top.addWidget(QLabel(T("Profil:")))
        self.profile = QComboBox()
        self.profile.setMinimumWidth(140)
        self.profile.activated.connect(self.apply_profile)
        top.addWidget(self.profile, 1)
        add_p = QPushButton(QIcon.fromTheme("list-add"), T("Yeni"))
        add_p.setToolTip(T("Mevcut ayarları profil olarak kaydet"))
        add_p.clicked.connect(self.save_profile)
        top.addWidget(add_p)
        self.del_p = QPushButton(QIcon.fromTheme("edit-delete"), T("Sil"))
        self.del_p.setToolTip(T("Seçili profili sil"))
        self.del_p.clicked.connect(self.delete_profile)
        top.addWidget(self.del_p)
        root.addLayout(top)

        tabs = QTabWidget()
        tabs.addTab(self.build_effect_tab(), QIcon.fromTheme("color-management"), T("Efekt"))
        tabs.addTab(self.build_schedule_tab(), QIcon.fromTheme("chronometer"), T("Zamanlama"))
        tabs.addTab(self.build_power_tab(), QIcon.fromTheme("battery"), T("Güç"))
        tabs.addTab(self.build_app_tab(), QIcon.fromTheme("configure"), T("Uygulama"))
        root.addWidget(tabs)

        # ----- bottom bar -----
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)
        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.defaults_btn = QPushButton(QIcon.fromTheme("edit-reset"), T("Varsayılanlar"))
        self.defaults_btn.clicked.connect(self.restore_defaults)
        self.revert_btn = QPushButton(QIcon.fromTheme("edit-undo"), T("Geri al"))
        self.revert_btn.setToolTip(T("Kaydedilmemiş değişiklikleri at"))
        self.revert_btn.clicked.connect(self.revert)
        self.save_btn = QPushButton(QIcon.fromTheme("document-save"), T("Kaydet"))
        self.save_btn.setDefault(True)
        self.save_btn.setToolTip(T("Ayarları kaydet (Ctrl+S)"))
        self.save_btn.clicked.connect(self.save)
        for b in (self.defaults_btn, self.revert_btn, self.save_btn):
            bottom.addWidget(b)
        root.addLayout(bottom)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save)

        # ----- timers -----
        self.preview_timer = QTimer(self, singleShot=True, interval=120)
        self.preview_timer.timeout.connect(self.write_preview)
        self.heartbeat = QTimer(self, interval=2000)
        self.heartbeat.timeout.connect(self.keep_preview_alive)
        self.heartbeat.start()
        self.anim = QTimer(self, interval=50)
        self.anim.timeout.connect(self.tick)
        self.anim.start()
        self.slow = QTimer(self, interval=2000)
        self.slow.timeout.connect(self.poll)
        self.slow.start()

        self.load_widgets()
        self.poll()

    # ================= tabs =================
    def build_effect_tab(self):
        w = QWidget()
        form = self.form = QFormLayout(w)

        self.effect = QComboBox()
        for key, name in EFFECT_NAMES.items():
            self.effect.addItem(name, key)
        self.effect.currentIndexChanged.connect(self.changed)
        form.addRow(T("Efekt:"), self.effect)

        self.color_row = QWidget()
        cr = QHBoxLayout(self.color_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.color_btn = QPushButton()
        self.color_btn.setToolTip(T("Özel renk seç"))
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.clicked.connect(self.pick_color)
        cr.addWidget(self.color_btn)
        cr.addSpacing(6)
        for c in PRESETS:
            b = QPushButton()
            b.setStyleSheet(swatch_style(c, 22))
            b.setToolTip("#" + c)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, c=c: self.set_color(c))
            cr.addWidget(b)
        cr.addStretch()
        form.addRow(T("Renk:"), self.color_row)

        self.cycle_row = QWidget()
        cy = QVBoxLayout(self.cycle_row)
        cy.setContentsMargins(0, 0, 0, 0)
        self.color_list = QListWidget()
        self.color_list.setFlow(QListWidget.Flow.LeftToRight)
        self.color_list.setFixedHeight(52)
        self.color_list.setSpacing(2)
        self.color_list.setIconSize(QSize(30, 30))
        self.color_list.itemDoubleClicked.connect(self.edit_cycle_color)
        cy.addWidget(self.color_list)
        btns = QHBoxLayout()
        add = QPushButton(QIcon.fromTheme("list-add"), T("Renk ekle"))
        add.clicked.connect(self.add_cycle_color)
        rem = QPushButton(QIcon.fromTheme("list-remove"), T("Seçileni sil"))
        rem.clicked.connect(self.remove_cycle_color)
        left = QPushButton(QIcon.fromTheme("go-previous"), "")
        left.setToolTip(T("Seçili rengi sola taşı"))
        left.clicked.connect(lambda: self.move_cycle_color(-1))
        right = QPushButton(QIcon.fromTheme("go-next"), "")
        right.setToolTip(T("Seçili rengi sağa taşı"))
        right.clicked.connect(lambda: self.move_cycle_color(1))
        for b in (left, right):
            b.setFixedWidth(40)
            if b.icon().isNull():
                b.setText("◀" if b is left else "▶")
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch()
        btns.addWidget(left)
        btns.addWidget(right)
        cy.addLayout(btns)
        hint = QLabel(T("Değiştirmek için renge çift tıklayın; sırayı oklarla değiştirin."))
        hint.setEnabled(False)
        cy.addWidget(hint)
        form.addRow(T("Renkler:"), self.cycle_row)

        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 100)
        self.speed.valueChanged.connect(self.changed)
        self.speed_lbl = QLabel()
        self.speed_lbl.setMinimumWidth(80)
        form.addRow(T("Hız:"), self.row(self.speed, self.speed_lbl))

        self.bright = QSlider(Qt.Orientation.Horizontal)
        self.bright.setRange(0, 100)
        self.bright.valueChanged.connect(self.changed)
        self.bright_lbl = QLabel()
        self.bright_lbl.setMinimumWidth(80)
        form.addRow(T("Parlaklık:"), self.row(self.bright, self.bright_lbl))
        return w

    def build_schedule_tab(self):
        w = QWidget()
        sf = QFormLayout(w)
        self.sched_on = QCheckBox(T("Işık sadece seçili günlerde, bu saatler arasında açık olsun"))
        self.sched_on.toggled.connect(self.changed)
        sf.addRow(self.sched_on)
        self.start = QTimeEdit()
        self.end = QTimeEdit()
        for te in (self.start, self.end):
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(self.changed)
        sf.addRow(T("Başlangıç:"), self.start)
        sf.addRow(T("Bitiş:"), self.end)
        days = QHBoxLayout()
        self.day_btns = []
        for name in DAY_NAMES:
            b = QPushButton(name)
            b.setCheckable(True)
            b.setFixedWidth(46)
            b.toggled.connect(self.changed)
            days.addWidget(b)
            self.day_btns.append(b)
        days.addStretch()
        sf.addRow(T("Günler:"), days)
        quick = QHBoxLayout()
        for label, sel in ((T("Her gün"), range(7)), (T("Hafta içi"), range(5)), (T("Hafta sonu"), (5, 6))):
            b = QPushButton(label)
            b.setFlat(True)
            b.clicked.connect(lambda _, sel=sel: self.set_days(sel))
            quick.addWidget(b)
        quick.addStretch()
        sf.addRow("", quick)
        self.sched_lbl = QLabel()
        self.sched_lbl.setWordWrap(True)
        sf.addRow(self.sched_lbl)
        return w

    def build_power_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.bat_mode = QComboBox()
        for key, name in BATTERY_MODES.items():
            self.bat_mode.addItem(name, key)
        self.bat_mode.currentIndexChanged.connect(self.changed)
        f.addRow(T("Şarj takılı değilken:"), self.bat_mode)
        self.bat_bright = QSlider(Qt.Orientation.Horizontal)
        self.bat_bright.setRange(5, 100)
        self.bat_bright.valueChanged.connect(self.changed)
        self.bat_bright_lbl = QLabel()
        self.bat_bright_lbl.setMinimumWidth(80)
        self.bat_row = self.row(self.bat_bright, self.bat_bright_lbl)
        f.addRow(T("Pilde en fazla:"), self.bat_row)
        self.power_lbl = QLabel()
        f.addRow(self.power_lbl)
        info = QLabel(T("Klavye ışığı pil ömrünü biraz kısaltır; pilde parlaklığı düşürmek iyi bir denge sağlar."))
        info.setWordWrap(True)
        info.setEnabled(False)
        f.addRow(info)
        return w

    def build_app_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox(T("Sistem tepsisi"))
        gv = QVBoxLayout(g)
        self.tray_on = QCheckBox(T("Sistem tepsisinde simge göster"))
        self.tray_on.setChecked(self.settings.value("tray", True, bool))
        self.tray_on.toggled.connect(self.app_setting_changed)
        self.close_to_tray = QCheckBox(T("Pencereyi kapatınca tepsiye küçült"))
        self.close_to_tray.setChecked(self.settings.value("close_to_tray", True, bool))
        self.close_to_tray.toggled.connect(self.app_setting_changed)
        self.autostart = QCheckBox(T("Oturum açılınca tepside başlat"))
        self.autostart.setChecked(os.path.exists(AUTOSTART_PATH))
        self.autostart.toggled.connect(self.set_autostart)
        for c in (self.tray_on, self.close_to_tray, self.autostart):
            gv.addWidget(c)
        v.addWidget(g)

        g2 = QGroupBox(T("Klavye kısayolu için komutlar"))
        g2v = QVBoxLayout(g2)
        cmds = QLabel(
            T("<code>victus-kbd --toggle</code> &nbsp;ışığı aç/kapat<br>"
              "<code>victus-kbd --profile Gece</code> &nbsp;profil seç<br>"
              "<code>victus-kbd --next-profile</code> &nbsp;sıradaki profil"))
        cmds.setWordWrap(True)
        cmds.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g2v.addWidget(cmds)
        kde = QLabel(T("KDE'de: Sistem Ayarları → Klavye → Kısayollar → Yeni Ekle → Komut veya Betik"))
        kde.setWordWrap(True)
        kde.setEnabled(False)
        g2v.addWidget(kde)
        v.addWidget(g2)
        v.addStretch()
        return w

    @staticmethod
    def row(*widgets):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            h.addWidget(x)
        return w

    # ================= state <-> widgets =================
    def load_widgets(self):
        c = self.cfg
        self.loading = True
        self.power.setChecked(c["enabled"])
        self.effect.setCurrentIndex(list(EFFECT_NAMES).index(c["effect"]))
        self.speed.setValue(c["speed"])
        self.bright.setValue(c["brightness"])
        s = c["schedule"]
        self.sched_on.setChecked(s["enabled"])
        self.start.setTime(QTime.fromString(s["start"], "HH:mm"))
        self.end.setTime(QTime.fromString(s["end"], "HH:mm"))
        for i, b in enumerate(self.day_btns):
            b.setChecked(i in s["days"])
        self.bat_mode.setCurrentIndex(list(BATTERY_MODES).index(c["battery"]["mode"]))
        self.bat_bright.setValue(c["battery"]["brightness"])
        self.loading = False
        self.refresh_cycle_list()
        self.sync_ui()

    def changed(self, *_):
        if self.loading:
            return
        c = self.cfg
        c["enabled"] = self.power.isChecked()
        c["effect"] = self.effect.currentData()
        c["speed"] = self.speed.value()
        c["brightness"] = self.bright.value()
        c["schedule"] = {
            "enabled": self.sched_on.isChecked(),
            "start": self.start.time().toString("HH:mm"),
            "end": self.end.time().toString("HH:mm"),
            "days": [i for i, b in enumerate(self.day_btns) if b.isChecked()],
        }
        c["battery"] = {"mode": self.bat_mode.currentData(),
                        "brightness": self.bat_bright.value()}
        self.sync_ui()
        self.preview_timer.start()

    def dirty(self):
        return self.cfg != self.saved

    def sync_ui(self):
        c = self.cfg
        e = c["effect"]
        self.form.setRowVisible(self.color_row, e in COLOR_EFFECTS)
        self.form.setRowVisible(self.cycle_row, e == "cycle")
        self.speed.setEnabled(e != "static")
        self.color_btn.setStyleSheet(swatch_style(c["color"]))
        if e == "static":
            self.speed_lbl.setText("—")
        elif e == "heartbeat":
            self.speed_lbl.setText(T("{n} nabız/dk").format(n=round(60 / heartbeat_period(c['speed']))))
        elif e == "fire":
            self.speed_lbl.setText(T("%{n}").format(n=c['speed']))
        else:
            self.speed_lbl.setText(T("{n} sn/tur").format(n=round(period(c['speed']))))
        self.bright_lbl.setText(T("%{n}").format(n=c['brightness']))
        self.power.setText(T("● Işık açık") if c["enabled"] else T("○ Işık kapalı"))
        self.power.setToolTip(T("Işığı kapatmak için tıklayın") if c["enabled"]
                              else T("Işığı açmak için tıklayın"))

        on = self.sched_on.isChecked()
        for w in [self.start, self.end] + self.day_btns:
            w.setEnabled(on)
        dim = c["battery"]["mode"] == "dim"
        self.bat_row.setEnabled(dim)
        self.bat_bright_lbl.setText(T("%{n}").format(n=c['battery']['brightness']))

        # Profile combo reflects whichever profile the current settings match
        self.profile.blockSignals(True)
        self.profile.clear()
        match = matching_profile(c)
        if match is None:
            self.profile.addItem(CUSTOM_PROFILE)
        for name in c["profiles"]:
            self.profile.addItem(name)
        self.profile.setCurrentText(match or CUSTOM_PROFILE)
        self.profile.blockSignals(False)
        self.del_p.setEnabled(match is not None)

        dirty = self.dirty()
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        self.setWindowTitle(("● " if dirty else "") + T("Victus Klavye Işığı"))
        self.update_status()
        self.update_labels()
        if self.tray:
            self.tray.sync()

    def update_labels(self):
        s = self.cfg["schedule"]
        days = s["days"]
        if not s["enabled"]:
            self.sched_lbl.setText(T("Zamanlama kapalı, ışık her zaman açık."))
        elif not days:
            self.sched_lbl.setText(T("⚠ Hiç gün seçilmedi, ışık hiç açılmayacak."))
        elif schedule_active(s):
            self.sched_lbl.setText(T("Şu an aralığın içinde: ışık açık ({start}–{end}).").format(**s))
        else:
            self.sched_lbl.setText(T("Şu an aralığın dışında: ışık kapalı. Açılış saati {start}.").format(**s))
        self.power_lbl.setText(T("Şu an: şarja takılı 🔌") if self.ac else T("Şu an: pilde 🔋"))

    # ================= save / revert / preview =================
    def config_mtime(self):
        try:
            return os.stat(CONFIG_PATH).st_mtime_ns
        except OSError:
            return None

    def write_disk(self, cfg):
        try:
            save_config(cfg)
        except OSError as e:
            QMessageBox.critical(self, T("Kaydedilemedi"), T("Ayar dosyası yazılamadı:\n{error}").format(error=e))
            return False
        self.cfg_mtime = self.config_mtime()
        return True

    def save(self):
        if not self.dirty():
            return
        if self.write_disk(self.cfg):
            self.saved = copy.deepcopy(self.cfg)
            remove_preview()
            self.sync_ui()
            self.flash_status(T("✔ Kaydedildi"))

    def revert(self):
        self.cfg = copy.deepcopy(self.saved)
        remove_preview()
        self.load_widgets()

    def restore_defaults(self):
        keep = self.cfg["profiles"]
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["profiles"] = keep
        self.load_widgets()
        self.preview_timer.start()

    def write_preview(self):
        if self.dirty():
            try:
                save_config(self.cfg, PREVIEW_PATH)
            except OSError:
                pass
        else:
            remove_preview()

    def keep_preview_alive(self):
        if self.dirty():
            self.write_preview()

    def update_status(self):
        if self.service_state != "active":
            self.status.setText(T("⚠ Servis çalışmıyor ({state}): sudo systemctl start victus-kbd").format(
                state=self.service_state))
        elif self.dirty():
            self.status.setText(T("Değişiklikler klavyede önizleniyor; kalıcı olması için Kaydet'e basın."))
        else:
            self.status.setText(T("Servis çalışıyor, ayarlar kaydedildi."))

    def flash_status(self, text):
        self.status.setText(text)
        QTimer.singleShot(2500, self.update_status)

    def confirm_discard(self):
        """Ask about unsaved changes; False means the user cancelled."""
        if not self.dirty():
            return True
        self.show_window()
        r = QMessageBox.question(
            self, T("Kaydedilmemiş değişiklikler"),
            T("Değişiklikler kaydedilmedi. Kaydetmek ister misiniz?"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            self.save()
            return not self.dirty()
        if r == QMessageBox.StandardButton.Discard:
            self.revert()
            return True
        return False

    # ================= profiles =================
    def apply_profile(self, index):
        name = self.profile.itemText(index)
        prof = self.cfg["profiles"].get(name)
        if prof:
            self.cfg.update(copy.deepcopy(prof))
            self.load_widgets()
            self.preview_timer.start()

    def save_profile(self):
        name, ok = QInputDialog.getText(self, T("Profil kaydet"), T("Profil adı:"),
                                        text=matching_profile(self.cfg) or "")
        name = name.strip()
        if not ok or not name:
            return
        if name == CUSTOM_PROFILE:
            QMessageBox.warning(self, T("Profil kaydet"), T("Bu ad kullanılamaz."))
            return
        if name in self.cfg["profiles"] and QMessageBox.question(
                self, T("Profil kaydet"), T("“{name}” zaten var. Üzerine yazılsın mı?").format(name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        self.set_profiles({**self.cfg["profiles"],
                           name: {k: copy.deepcopy(self.cfg[k]) for k in PROFILE_KEYS}})

    def delete_profile(self):
        name = matching_profile(self.cfg)
        if name and QMessageBox.question(self, T("Profili sil"), T("“{name}” silinsin mi?").format(name=name)) \
                == QMessageBox.StandardButton.Yes:
            self.set_profiles({k: v for k, v in self.cfg["profiles"].items() if k != name})

    def set_profiles(self, profiles):
        # Profile list changes are saved right away; lighting settings still need T("Kaydet").
        self.cfg["profiles"] = profiles
        saved = copy.deepcopy(self.saved)
        saved["profiles"] = copy.deepcopy(profiles)
        if self.write_disk(saved):
            self.saved = saved
        self.sync_ui()

    # ================= colors =================
    def set_color(self, hexcolor):
        self.cfg["color"] = hexcolor
        self.changed()

    def pick_color(self):
        col = QColorDialog.getColor(QColor("#" + self.cfg["color"]), self, T("Renk seç"))
        if col.isValid():
            self.set_color(col.name()[1:])

    def refresh_cycle_list(self):
        self.color_list.clear()
        for c in self.cfg["colors"]:
            # Drawn as an icon so the selection highlight doesn't hide the color
            it = QListWidgetItem(color_icon(c), "")
            it.setToolTip("#" + c)
            it.setData(Qt.ItemDataRole.UserRole, c)
            self.color_list.addItem(it)

    def move_cycle_color(self, step):
        i = self.color_list.currentRow()
        j = i + step
        colors = self.cfg["colors"]
        if i < 0 or not 0 <= j < len(colors):
            return
        colors[i], colors[j] = colors[j], colors[i]
        self.refresh_cycle_list()
        self.color_list.setCurrentRow(j)
        self.changed()

    def add_cycle_color(self):
        col = QColorDialog.getColor(QColor("#ffffff"), self, T("Renk ekle"))
        if col.isValid():
            self.cfg["colors"].append(col.name()[1:])
            self.refresh_cycle_list()
            self.changed()

    def edit_cycle_color(self, item):
        i = self.color_list.row(item)
        col = QColorDialog.getColor(QColor("#" + self.cfg["colors"][i]), self, T("Rengi değiştir"))
        if col.isValid():
            self.cfg["colors"][i] = col.name()[1:]
            self.refresh_cycle_list()
            self.changed()

    def remove_cycle_color(self):
        i = self.color_list.currentRow()
        if i < 0:
            return
        if len(self.cfg["colors"]) <= 1:
            QMessageBox.information(self, T("Renk geçişi"), T("En az bir renk kalmalı."))
            return
        del self.cfg["colors"][i]
        self.refresh_cycle_list()
        self.changed()

    def set_days(self, sel):
        self.loading = True
        for i, b in enumerate(self.day_btns):
            b.setChecked(i in sel)
        self.loading = False
        self.changed()

    # ================= timers =================
    def current_rgb(self):
        on, bright = resolve(self.cfg, ac=self.ac)
        if not on:
            return None
        # Wall-clock time is the shared time base, so the preview matches the keyboard.
        return compute_color(self.cfg, time.time(), bright)

    def off_reason(self):
        c = self.cfg
        if not c["enabled"]:
            return T("Kapalı")
        if not self.ac and c["battery"]["mode"] == "off":
            return T("Kapalı · pilde")
        if not schedule_active(c["schedule"]):
            if not c["schedule"]["days"]:
                return T("Kapalı · zamanlamada gün seçilmedi")
            return T("Kapalı · zamanlama dışında ({start}'da açılır)").format(**c["schedule"])
        return T("Kapalı · parlaklık %0")

    def tick(self):
        if not self.isVisible():
            return
        rgb = self.current_rgb()
        if rgb:
            col, text = "#%02x%02x%02x" % rgb, ""
        else:
            col, text = "#1e1e1e", self.off_reason()
        self.preview.setStyleSheet(f"QFrame {{ background:{col}; border-radius:10px; }}")
        self.preview_lbl.setText(text)

    def poll(self):
        self.ac = on_ac_power()
        # Pick up changes made elsewhere (CLI, tray of another session, hand edits)
        m = self.config_mtime()
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            new = load_config()
            if self.dirty():
                self.saved = new
            else:
                self.saved, self.cfg = new, copy.deepcopy(new)
                self.load_widgets()
        r = subprocess.run(["systemctl", "is-active", "victus-kbd.service"],
                           capture_output=True, text=True)
        self.service_state = r.stdout.strip()
        self.update_status()
        self.update_labels()
        if self.tray:
            self.tray.sync()

    # ================= window / app settings =================
    def app_setting_changed(self):
        self.settings.setValue("tray", self.tray_on.isChecked())
        self.settings.setValue("close_to_tray", self.close_to_tray.isChecked())
        self.close_to_tray.setEnabled(self.tray_on.isChecked())
        if self.tray:
            self.tray.setVisible(self.tray_on.isChecked())

    def set_autostart(self, on):
        if on:
            os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
            with open(AUTOSTART_PATH, "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName={T('Victus Klavye Işığı')}\n"
                        "Exec=victus-kbd --tray\nIcon=input-keyboard\n"
                        "X-GNOME-Autostart-enabled=true\n")
        else:
            try:
                os.remove(AUTOSTART_PATH)
            except FileNotFoundError:
                pass

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        if not self.confirm_discard():
            ev.ignore()
            return
        if (self.tray and self.tray.isVisible()
                and self.close_to_tray.isChecked()):
            ev.ignore()
            self.hide()
            return
        remove_preview()
        ev.accept()
        QApplication.quit()

    def quit_app(self):
        if self.confirm_discard():
            remove_preview()
            QApplication.quit()


class Tray(QSystemTrayIcon):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.last_rgb = ()
        self.setToolTip(T("Victus Klavye Işığı"))
        menu = QMenu()
        menu.addAction(QIcon.fromTheme("configure"), T("Ayarları aç"), win.show_window)
        menu.addSeparator()
        self.toggle_act = menu.addAction(T("Işık açık"))
        self.toggle_act.setCheckable(True)
        self.toggle_act.triggered.connect(self.toggle)
        self.profiles_menu = menu.addMenu(T("Profiller"))
        self.profiles_menu.aboutToShow.connect(self.fill_profiles)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("application-exit"), T("Çıkış"), win.quit_app)
        self.setContextMenu(menu)
        self.activated.connect(self.on_activated)
        self.anim = QTimer(self, interval=250)
        self.anim.timeout.connect(self.update_icon)
        self.anim.start()
        self.update_icon()

    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.win.isVisible():
                if self.win.confirm_discard():
                    self.win.hide()
            else:
                self.win.show_window()

    def update_icon(self):
        rgb = self.win.current_rgb()
        if rgb != self.last_rgb:
            self.setIcon(dot_icon(rgb))
            self.last_rgb = rgb

    def sync(self):
        self.toggle_act.setChecked(self.win.saved["enabled"])

    def fill_profiles(self):
        self.profiles_menu.clear()
        current = matching_profile(self.win.saved)
        for name in self.win.saved["profiles"]:
            a = self.profiles_menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == current)
            a.triggered.connect(lambda _, n=name: self.pick(n))

    def apply_saved(self, mutate):
        """Change the saved config right away (tray actions don't wait for Kaydet)."""
        w = self.win
        for cfg in (w.saved, w.cfg):
            mutate(cfg)
        w.write_disk(w.saved)
        w.load_widgets()
        w.write_preview()

    def toggle(self, on):
        self.apply_saved(lambda c: c.update(enabled=on))

    def pick(self, name):
        prof = copy.deepcopy(self.win.saved["profiles"][name])
        self.apply_saved(lambda c: c.update(copy.deepcopy(prof), enabled=True))


# ================= command line =================
def run_cli(args):
    try:
        cfg = load_config()
        names = list(cfg["profiles"])
        if args.list_profiles:
            current = matching_profile(cfg)
            for n in names:
                print(("* " if n == current else "  ") + n)
            return 0
        if args.toggle:
            cfg["enabled"] = not cfg["enabled"]
        elif args.on or args.off:
            cfg["enabled"] = bool(args.on)
        elif args.profile or args.next_profile:
            if args.next_profile:
                if not names:
                    print(T("Hiç profil yok."), file=sys.stderr)
                    return 1
                cur = matching_profile(cfg)
                name = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
            else:
                name = args.profile
                if name not in cfg["profiles"]:
                    print(T("Profil bulunamadı: {name}. Mevcut: {names}").format(name=name, names=", ".join(names)), file=sys.stderr)
                    return 1
            cfg.update(copy.deepcopy(cfg["profiles"][name]), enabled=True)
            print(name)
        save_config(cfg)
    except OSError as e:
        print(T("Ayar dosyası yazılamadı: {error}").format(error=e), file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(prog="victus-kbd", description=T("HP Victus klavye ışığı"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--toggle", action="store_true", help=T("ışığı aç/kapat"))
    g.add_argument("--on", action="store_true", help=T("ışığı aç"))
    g.add_argument("--off", action="store_true", help=T("ışığı kapat"))
    g.add_argument("--profile", metavar=T("AD"), help=T("profili uygula"))
    g.add_argument("--next-profile", action="store_true", help=T("sıradaki profile geç"))
    g.add_argument("--list-profiles", action="store_true", help=T("profilleri listele"))
    ap.add_argument("--tray", action="store_true", help=T("pencereyi açmadan tepside başlat"))
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}",
                    help=T("sürümü göster"))
    args = ap.parse_args()
    if any((args.toggle, args.on, args.off, args.profile, args.next_profile, args.list_profiles)):
        sys.exit(run_cli(args))

    app = QApplication(sys.argv)
    app.setApplicationName(T("Victus Klavye Işığı"))
    app.setDesktopFileName("victus-kbd")
    app.setQuitOnLastWindowClosed(False)

    # Single instance: a second launch just brings the existing window up.
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)

    w = Window()
    # Always create the tray icon: at login the panel may not be up yet, and Qt
    # shows the icon as soon as it is.
    w.tray = Tray(w)
    w.tray.setVisible(w.tray_on.isChecked())
    w.tray.sync()
    w.close_to_tray.setEnabled(w.tray_on.isChecked())
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w.show_window()))

    if not (args.tray and w.tray and w.tray.isVisible()):
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
