# Point the app at a throwaway config dir before any victus_kbd module is imported.
import os
import tempfile

TEST_DIR = tempfile.mkdtemp(prefix="victus-kbd-test-")
os.environ["VICTUS_KBD_CONFIG_DIR"] = TEST_DIR
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# The tests check Turkish UI strings; test_i18n covers English separately.
os.environ["VICTUS_KBD_LANG"] = "tr"


def clean_config_dir():
    for name in os.listdir(TEST_DIR):
        os.remove(os.path.join(TEST_DIR, name))
