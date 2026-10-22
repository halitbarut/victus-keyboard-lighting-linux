import copy
import io
import os
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests import clean_config_dir
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import QApplication, QColorDialog, QInputDialog, QMessageBox

from victus_kbd import gui
from victus_kbd.effects import (
    CONFIG_PATH, DEFAULTS, PREVIEW_PATH, compute_color, load_config, save_config,
)

app = QApplication.instance() or QApplication([])
Btn = QMessageBox.StandardButton


def answer(button):
    return mock.patch.object(QMessageBox, "question", return_value=button)


class GuiTest(unittest.TestCase):
    def setUp(self):
        clean_config_dir()
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(effect="static", color="ff0000")
        save_config(cfg)
        # Keep the real tray/autostart settings of the user untouched
        self.settings = mock.patch.object(gui, "QSettings", return_value=_MemSettings())
        self.settings.start()
        self.autostart = mock.patch.object(gui, "AUTOSTART_PATH",
                                           os.path.join(os.path.dirname(CONFIG_PATH), "autostart.desktop"))
        self.autostart.start()
        self.w = gui.Window()
        self.w.tray = gui.Tray(self.w)

    def tearDown(self):
        self.w.anim.stop()
        self.w.slow.stop()
        self.w.heartbeat.stop()
        self.w.tray.anim.stop()
        self.w.tray.hide()
        self.w.hide()
        self.w.deleteLater()
        self.settings.stop()
        self.autostart.stop()
        app.processEvents()

    def flush_preview(self):
        self.w.preview_timer.stop()
        self.w.write_preview()

    # ----- save / revert -----
    def test_starts_clean(self):
        self.assertFalse(self.w.dirty())
        self.assertFalse(self.w.save_btn.isEnabled())
        self.assertFalse(self.w.revert_btn.isEnabled())
        self.assertNotIn("●", self.w.windowTitle())

    def test_edit_previews_then_saves(self):
        self.w.bright.setValue(40)
        self.assertTrue(self.w.dirty())
        self.assertTrue(self.w.save_btn.isEnabled())
        self.assertIn("●", self.w.windowTitle())
        self.flush_preview()
        self.assertEqual(load_config(PREVIEW_PATH)["brightness"], 40)
        self.assertEqual(load_config()["brightness"], 100)  # not saved yet
        self.w.save()
        self.assertEqual(load_config()["brightness"], 40)
        self.assertFalse(os.path.exists(PREVIEW_PATH))
        self.assertFalse(self.w.dirty())
        self.assertFalse(self.w.save_btn.isEnabled())

    def test_changing_back_is_not_dirty(self):
        self.w.bright.setValue(40)
        self.w.bright.setValue(100)
        self.assertFalse(self.w.dirty())
        self.flush_preview()
        self.assertFalse(os.path.exists(PREVIEW_PATH))

    def test_revert(self):
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("fire"))
        self.w.speed.setValue(3)
        self.flush_preview()
        self.w.revert()
        self.assertFalse(self.w.dirty())
        self.assertEqual(self.w.effect.currentData(), "static")
        self.assertEqual(self.w.speed.value(), DEFAULTS["speed"])
        self.assertFalse(os.path.exists(PREVIEW_PATH))

    def test_defaults_keep_profiles(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("Benim", True)):
            self.w.save_profile()
        self.w.restore_defaults()
        self.assertEqual(self.w.cfg["effect"], DEFAULTS["effect"])
        self.assertIn("Benim", self.w.cfg["profiles"])

    def test_every_widget_marks_dirty(self):
        w = self.w
        actions = [
            lambda: w.power.click(),
            lambda: w.effect.setCurrentIndex(1),
            lambda: w.speed.setValue(10),
            lambda: w.bright.setValue(10),
            lambda: w.set_color("00ff00"),
            lambda: w.sched_on.setChecked(True),
            lambda: w.start.setTime(w.start.time().addSecs(3600)),
            lambda: w.end.setTime(w.end.time().addSecs(3600)),
            lambda: (w.saved["schedule"].update(enabled=True), w.revert(), w.day_btns[2].click()),
            lambda: w.bat_mode.setCurrentIndex(2),
            lambda: w.bat_bright.setValue(77),
        ]
        for act in actions:
            w.saved = load_config()
            w.revert()
            act()
            self.assertTrue(w.dirty(), actions.index(act))

    # ----- effects ui -----
    def test_rows_follow_effect(self):
        for effect in gui.EFFECT_NAMES:
            self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index(effect))
            self.assertEqual(self.w.form.isRowVisible(self.w.color_row), effect in gui.COLOR_EFFECTS)
            self.assertEqual(self.w.form.isRowVisible(self.w.cycle_row), effect == "cycle")
            self.assertEqual(self.w.speed.isEnabled(), effect != "static")

    def test_speed_labels(self):
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("heartbeat"))
        self.w.speed.setValue(1)
        self.assertEqual(self.w.speed_lbl.text(), "40 nabız/dk")
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("rainbow"))
        self.w.speed.setValue(100)
        self.assertEqual(self.w.speed_lbl.text(), "2 sn/tur")

    def test_cycle_colors(self):
        w = self.w
        w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("cycle"))
        with mock.patch.object(QColorDialog, "getColor", return_value=QColor("#abcdef")):
            w.add_cycle_color()
        self.assertEqual(w.cfg["colors"], ["ff0000", "00ff00", "0000ff", "abcdef"])
        w.color_list.setCurrentRow(3)
        w.move_cycle_color(-1)
        self.assertEqual(w.cfg["colors"], ["ff0000", "00ff00", "abcdef", "0000ff"])
        self.assertEqual(w.color_list.currentRow(), 2)
        w.color_list.setCurrentRow(0)
        w.move_cycle_color(-1)  # already first: no change
        self.assertEqual(w.cfg["colors"][0], "ff0000")
        with mock.patch.object(QColorDialog, "getColor", return_value=QColor("#111111")):
            w.edit_cycle_color(w.color_list.item(1))
        self.assertEqual(w.cfg["colors"][1], "111111")
        for _ in range(5):
            w.color_list.setCurrentRow(0)
            with mock.patch.object(QMessageBox, "information"):
                w.remove_cycle_color()
        self.assertEqual(len(w.cfg["colors"]), 1)  # last color is kept
        self.assertEqual(w.color_list.count(), 1)
        self.assertTrue(w.dirty())

    def test_cancelled_color_dialog_changes_nothing(self):
        with mock.patch.object(QColorDialog, "getColor", return_value=QColor()):
            self.w.pick_color()
            self.w.add_cycle_color()
        self.assertFalse(self.w.dirty())

    def test_day_shortcuts(self):
        self.w.sched_on.setChecked(True)
        self.w.set_days(range(5))
        self.assertEqual(self.w.cfg["schedule"]["days"], [0, 1, 2, 3, 4])
        self.w.set_days((5, 6))
        self.assertEqual(self.w.cfg["schedule"]["days"], [5, 6])

    # ----- preview strip -----
    def test_preview_matches_keyboard_time_base(self):
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("rainbow"))
        self.w.speed.setValue(100)
        with mock.patch.object(gui.time, "time", return_value=1234.5):
            self.assertEqual(self.w.current_rgb(), compute_color(self.w.cfg, 1234.5))

    def test_off_reasons(self):
        self.w.ac = True
        self.w.power.click()
        self.assertIsNone(self.w.current_rgb())
        self.assertEqual(self.w.off_reason(), "Kapalı")
        self.w.power.click()
        self.w.bat_mode.setCurrentIndex(list(gui.BATTERY_MODES).index("off"))
        self.w.ac = False
        self.assertEqual(self.w.off_reason(), "Kapalı · pilde")

    # ----- profiles -----
    def test_profile_apply_and_match(self):
        w = self.w
        w.apply_profile(w.profile.findText("Şömine"))
        self.assertEqual(w.effect.currentData(), "fire")
        self.assertEqual(w.profile.currentText(), "Şömine")
        self.assertTrue(w.del_p.isEnabled())
        w.bright.setValue(5)
        self.assertEqual(w.profile.currentText(), gui.CUSTOM_PROFILE)
        self.assertFalse(w.del_p.isEnabled())

    def test_profile_save_and_delete_persist_immediately(self):
        w = self.w
        w.bright.setValue(33)
        with mock.patch.object(QInputDialog, "getText", return_value=("Yeni Profil", True)):
            w.save_profile()
        self.assertIn("Yeni Profil", load_config()["profiles"])
        self.assertEqual(load_config()["brightness"], 100)  # lighting itself still unsaved
        self.assertEqual(w.profile.currentText(), "Yeni Profil")
        with answer(Btn.Yes):
            w.delete_profile()
        self.assertNotIn("Yeni Profil", load_config()["profiles"])

    def test_profile_bad_names(self):
        before = dict(load_config()["profiles"])
        for name in ("", "   ", gui.CUSTOM_PROFILE):
            with mock.patch.object(QInputDialog, "getText", return_value=(name, True)), \
                    mock.patch.object(QMessageBox, "warning"):
                self.w.save_profile()
        self.assertEqual(load_config()["profiles"], before)

    def test_profile_overwrite_needs_confirmation(self):
        self.w.bright.setValue(12)
        with mock.patch.object(QInputDialog, "getText", return_value=("Gece", True)), answer(Btn.No):
            self.w.save_profile()
        self.assertEqual(load_config()["profiles"]["Gece"]["brightness"], 20)

    # ----- external changes -----
    def test_external_change_reloads_when_clean(self):
        cfg = load_config()
        cfg["color"] = "00ff00"
        save_config(cfg)
        os.utime(CONFIG_PATH, ns=(0, time.time_ns() + 5_000_000))
        self.w.poll()
        self.assertEqual(self.w.cfg["color"], "00ff00")
        self.assertFalse(self.w.dirty())

    def test_external_change_keeps_unsaved_edits(self):
        self.w.bright.setValue(50)
        cfg = load_config()
        cfg["enabled"] = False
        save_config(cfg)
        os.utime(CONFIG_PATH, ns=(0, time.time_ns() + 5_000_000))
        self.w.poll()
        self.assertEqual(self.w.cfg["brightness"], 50)
        self.assertFalse(self.w.saved["enabled"])

    # ----- tray -----
    def test_tray_toggle_and_profile(self):
        t = self.w.tray
        t.toggle(False)
        self.assertFalse(load_config()["enabled"])
        self.assertFalse(self.w.power.isChecked())
        t.toggle(True)
        self.assertTrue(load_config()["enabled"])
        t.pick("Gece")
        c = load_config()
        self.assertEqual((c["effect"], c["color"], c["brightness"]), ("static", "ff6a00", 20))
        self.assertFalse(self.w.dirty())
        t.fill_profiles()
        checked = [a.text() for a in t.profiles_menu.actions() if a.isChecked()]
        self.assertEqual(checked, ["Gece"])

    def test_tray_toggle_keeps_unsaved_edits(self):
        self.w.bright.setValue(50)
        self.w.tray.toggle(False)
        self.assertEqual(load_config()["brightness"], 100)
        self.assertEqual(self.w.cfg["brightness"], 50)
        self.assertTrue(self.w.dirty())

    # ----- closing -----
    def test_confirm_discard(self):
        self.w.bright.setValue(50)
        with answer(Btn.Cancel):
            self.assertFalse(self.w.confirm_discard())
        self.assertTrue(self.w.dirty())
        with answer(Btn.Discard):
            self.assertTrue(self.w.confirm_discard())
        self.assertEqual(self.w.cfg["brightness"], 100)
        self.w.bright.setValue(60)
        with answer(Btn.Save):
            self.assertTrue(self.w.confirm_discard())
        self.assertEqual(load_config()["brightness"], 60)

    def test_close_hides_to_tray(self):
        self.w.show()
        self.w.tray.setVisible(True)
        if not self.w.tray.isVisible():
            self.skipTest("no system tray in this environment")
        ev = QCloseEvent()
        self.w.closeEvent(ev)
        self.assertFalse(ev.isAccepted())
        self.assertFalse(self.w.isVisible())

    def test_cancelled_close_keeps_window(self):
        self.w.show()
        self.w.bright.setValue(50)
        ev = QCloseEvent()
        with answer(Btn.Cancel):
            self.w.closeEvent(ev)
        self.assertFalse(ev.isAccepted())
        self.assertTrue(self.w.isVisible())

    def test_language_choice_saves_and_restarts(self):
        self.assertEqual(self.w.language.currentData(), "auto")
        with mock.patch.object(gui.Window, "restart_app") as restart, \
                mock.patch.object(QMessageBox, "information"):
            self.w.language.setCurrentIndex(self.w.language.findData("en"))
            self.assertEqual(self.w.settings.value("language"), "en")
            restart.assert_called_once()
            restart.reset_mock()
            self.w.language.setCurrentIndex(self.w.language.findData("tr"))  # already Turkish
            self.assertEqual(self.w.settings.value("language"), "tr")
            restart.assert_not_called()

    def test_language_change_can_be_cancelled(self):
        self.w.bright.setValue(50)
        with mock.patch.object(gui.Window, "restart_app") as restart, answer(Btn.Cancel):
            self.w.language.setCurrentIndex(self.w.language.findData("en"))
        restart.assert_not_called()
        self.assertTrue(self.w.dirty())

    def test_restart_reexecs_without_tray_flag(self):
        with mock.patch.object(gui.os, "execv") as execv, \
                mock.patch.object(gui.sys, "argv", ["victus-kbd", "--tray"]):
            self.w.restart_app()
        args = execv.call_args[0][1]
        self.assertEqual(args[1:], ["-m", "victus_kbd.gui"])

    def test_preview_animation_only_while_shown(self):
        self.assertFalse(self.w.anim.isActive())
        self.w.show()
        self.assertTrue(self.w.anim.isActive())
        self.w.hide()
        self.assertFalse(self.w.anim.isActive())

    def test_hidden_window_does_not_run_systemctl(self):
        with mock.patch.object(gui.subprocess, "run") as run:
            self.w.hide()
            self.w.poll()
            run.assert_not_called()
            self.w.show()
            self.assertTrue(run.called)

    def test_preview_strip_repaints_only_on_change(self):
        strip = self.w.preview
        with mock.patch.object(strip, "update") as update:
            strip.set_color(QColor("#123456"))
            strip.set_color(QColor("#123456"))
        self.assertEqual(update.call_count, 1)

    def test_tray_slows_down_for_steady_color(self):
        t = self.w.tray
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("static"))
        t.update_icon()
        self.assertEqual(t.anim.interval(), 2000)
        self.w.effect.setCurrentIndex(list(gui.EFFECT_NAMES).index("rainbow"))
        t.update_icon()
        self.assertEqual(t.anim.interval(), 250)
        self.w.power.click()  # light off: nothing moves
        t.update_icon()
        self.assertEqual(t.anim.interval(), 2000)

    def test_autostart_toggle(self):
        self.w.autostart.setChecked(True)
        with open(gui.AUTOSTART_PATH) as f:
            self.assertIn("Exec=victus-kbd --tray", f.read())
        self.w.autostart.setChecked(False)
        self.assertFalse(os.path.exists(gui.AUTOSTART_PATH))


class CliTest(unittest.TestCase):
    def setUp(self):
        clean_config_dir()

    def run_cli(self, **kw):
        args = dict(toggle=False, on=False, off=False, profile=None,
                    next_profile=False, list_profiles=False)
        args.update(kw)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gui.run_cli(Namespace(**args))
        return rc, out.getvalue(), err.getvalue()

    def test_toggle_on_off(self):
        self.assertEqual(self.run_cli(toggle=True)[0], 0)
        self.assertFalse(load_config()["enabled"])
        self.run_cli(toggle=True)
        self.assertTrue(load_config()["enabled"])
        self.run_cli(off=True)
        self.assertFalse(load_config()["enabled"])
        self.run_cli(on=True)
        self.assertTrue(load_config()["enabled"])

    def test_profiles(self):
        rc, out, _ = self.run_cli(profile="Gece")
        self.assertEqual((rc, out.strip()), (0, "Gece"))
        self.assertEqual(load_config()["color"], "ff6a00")
        rc, out, _ = self.run_cli(next_profile=True)
        self.assertEqual(out.strip(), "Sakin")
        rc, _, err = self.run_cli(profile="Yok")
        self.assertEqual(rc, 1)
        self.assertIn("Profil bulunamadı", err)
        rc, out, _ = self.run_cli(list_profiles=True)
        self.assertIn("* Sakin", out)

    def test_next_profile_wraps(self):
        self.run_cli(profile="Şömine")
        _, out, _ = self.run_cli(next_profile=True)
        self.assertEqual(out.strip(), "Gökkuşağı")


class _MemSettings:
    def __init__(self):
        self.d = {}

    def value(self, key, default=None, type=None):
        return self.d.get(key, default)

    def setValue(self, key, value):
        self.d[key] = value

    def sync(self):
        pass


if __name__ == "__main__":
    unittest.main()
