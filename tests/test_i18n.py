import ast
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  (sets up the test environment)
from victus_kbd.i18n import EN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def translated_literals(path):
    """Every string passed directly to T(...) in a source file."""
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "T" and node.args
                and isinstance(node.args[0], ast.Constant)):
            yield node.args[0].value


class I18nTest(unittest.TestCase):
    def test_every_string_has_english(self):
        missing = []
        for name in ("gui.py", "effects.py"):
            for text in translated_literals(os.path.join(ROOT, "victus_kbd", name)):
                if text not in EN:
                    missing.append((name, text))
        self.assertEqual(missing, [])

    def test_placeholders_match(self):
        import string
        fmt = string.Formatter()
        for tr, en in EN.items():
            names = lambda s: sorted(f for _, f, _, _ in fmt.parse(s) if f)
            self.assertEqual(names(tr), names(en), tr)

    def test_saved_language_is_used(self):
        from victus_kbd import i18n
        d = tempfile.mkdtemp()
        path = os.path.join(d, "gui.conf")
        env = {k: v for k, v in os.environ.items() if k != "VICTUS_KBD_LANG"}
        with mock.patch.object(i18n, "SETTINGS_PATH", path), \
                mock.patch.dict(os.environ, env, clear=True):
            os.environ["LANG"] = "en_US.UTF-8"
            self.assertEqual(i18n.detect_language(), "en")       # no file: follow the system
            with open(path, "w") as f:
                f.write("[General]\nlanguage=tr\n")
            self.assertEqual(i18n.detect_language(), "tr")       # saved choice wins
            with open(path, "w") as f:
                f.write("[General]\nlanguage=auto\n")
            self.assertEqual(i18n.detect_language(), "en")
            os.environ["VICTUS_KBD_LANG"] = "tr"
            self.assertEqual(i18n.detect_language(), "tr")       # env var wins over everything
            with open(path, "w") as f:
                f.write("not an ini file")
            del os.environ["VICTUS_KBD_LANG"]
            self.assertEqual(i18n.detect_language(), "en")       # broken file is ignored

    def run_lang(self, lang, code):
        env = dict(os.environ, VICTUS_KBD_LANG=lang, QT_QPA_PLATFORM="offscreen",
                   PYTHONPATH=ROOT, VICTUS_KBD_CONFIG_DIR=tempfile.mkdtemp())
        r = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_english_ui_starts(self):
        out = self.run_lang("en", (
            "from PyQt6.QtWidgets import QApplication\n"
            "app = QApplication([])\n"
            "from victus_kbd import gui\n"
            "w = gui.Window(); w.sync_ui()\n"
            "print(w.windowTitle(), '|', w.save_btn.text(), '|', list(w.cfg['profiles'])[0])\n"))
        self.assertEqual(out, "Victus Keyboard Lighting | Save | Rainbow")

    def test_english_cli(self):
        out = self.run_lang("en", "import sys; sys.argv=['victus-kbd','--help']\n"
                                  "from victus_kbd import gui\n"
                                  "try:\n    gui.main()\nexcept SystemExit:\n    pass\n")
        self.assertIn("turn the light on/off", out)


if __name__ == "__main__":
    unittest.main()
