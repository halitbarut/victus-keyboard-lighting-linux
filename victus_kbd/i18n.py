"""Tiny translation layer: Turkish source strings, English for every other locale.

The language comes from VICTUS_KBD_LANG, else the choice saved in the app
(Settings → App → Language), else the usual locale variables.
To add a language, add a dict like EN below and pick it in detect_language().
"""
import configparser
import os

LANGUAGES = ("tr", "en")
# Same file QSettings("victus-kbd", "gui") uses, readable before Qt starts
SETTINGS_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                             "victus-kbd", "gui.conf")


def saved_language():
    """The language picked in the app, or None for "follow the system"."""
    cp = configparser.ConfigParser()
    try:
        cp.read(SETTINGS_PATH)
    except configparser.Error:
        return None
    value = cp.get("General", "language", fallback="auto")
    return value if value in LANGUAGES else None


def detect_language():
    forced = os.environ.get("VICTUS_KBD_LANG", "")
    if forced:
        return "tr" if forced.lower().startswith("tr") else "en"
    return saved_language() or system_language()


def system_language():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            return "tr" if value.lower().startswith("tr") else "en"
    return "en"


LANG = detect_language()

EN = {
    # effects / modes / days
    "Sabit renk": "Static color",
    "Gökkuşağı": "Rainbow",
    "Nefes": "Breathing",
    "Renk geçişi": "Color cycle",
    "Ateş": "Fire",
    "Kalp atışı": "Heartbeat",
    "Aynı kalsın": "Keep the same",
    "Parlaklığı düşür": "Lower brightness",
    "Işığı kapat": "Turn the light off",
    "Pzt": "Mon", "Sal": "Tue", "Çar": "Wed", "Per": "Thu",
    "Cum": "Fri", "Cmt": "Sat", "Paz": "Sun",
    "— Özel —": "— Custom —",
    # default profile names
    "Oyun": "Gaming",
    "Gece": "Night",
    "Sakin": "Calm",
    "Şömine": "Fireplace",
    # main window
    "Victus Klavye Işığı": "Victus Keyboard Lighting",
    "Profil:": "Profile:",
    "Yeni": "New",
    "Mevcut ayarları profil olarak kaydet": "Save the current settings as a profile",
    "Sil": "Delete",
    "Seçili profili sil": "Delete the selected profile",
    "Efekt": "Effect",
    "Zamanlama": "Schedule",
    "Güç": "Power",
    "Uygulama": "App",
    "Varsayılanlar": "Defaults",
    "Geri al": "Revert",
    "Kaydedilmemiş değişiklikleri at": "Discard unsaved changes",
    "Kaydet": "Save",
    "Ayarları kaydet (Ctrl+S)": "Save settings (Ctrl+S)",
    "Efekt:": "Effect:",
    "Özel renk seç": "Pick a custom color",
    "Renk:": "Color:",
    "Renk ekle": "Add color",
    "Seçileni sil": "Remove selected",
    "Seçili rengi sola taşı": "Move the selected color left",
    "Seçili rengi sağa taşı": "Move the selected color right",
    "Değiştirmek için renge çift tıklayın; sırayı oklarla değiştirin.":
        "Double-click a color to change it; use the arrows to reorder.",
    "Renkler:": "Colors:",
    "Hız:": "Speed:",
    "Parlaklık:": "Brightness:",
    "{n} nabız/dk": "{n} bpm",
    "{n} sn/tur": "{n} s/cycle",
    "%{n}": "{n}%",
    "● Işık açık": "● Light on",
    "○ Işık kapalı": "○ Light off",
    "Işığı kapatmak için tıklayın": "Click to turn the light off",
    "Işığı açmak için tıklayın": "Click to turn the light on",
    # schedule tab
    "Işık sadece seçili günlerde, bu saatler arasında açık olsun":
        "Only turn the light on during these hours on the selected days",
    "Başlangıç:": "Start:",
    "Bitiş:": "End:",
    "Günler:": "Days:",
    "Her gün": "Every day",
    "Hafta içi": "Weekdays",
    "Hafta sonu": "Weekend",
    "Zamanlama kapalı, ışık her zaman açık.": "Schedule is off, the light is always on.",
    "⚠ Hiç gün seçilmedi, ışık hiç açılmayacak.": "⚠ No days selected, the light will never turn on.",
    "Şu an aralığın içinde: ışık açık ({start}–{end}).":
        "Inside the time range now: light is on ({start}–{end}).",
    "Şu an aralığın dışında: ışık kapalı. Açılış saati {start}.":
        "Outside the time range now: light is off. It turns on at {start}.",
    # power tab
    "Şarj takılı değilken:": "When not charging:",
    "Pilde en fazla:": "Maximum on battery:",
    "Şu an: şarja takılı 🔌": "Now: plugged in 🔌",
    "Şu an: pilde 🔋": "Now: on battery 🔋",
    "Klavye ışığı pil ömrünü biraz kısaltır; pilde parlaklığı düşürmek iyi bir denge sağlar.":
        "Keyboard lighting uses some battery; lowering brightness on battery is a good balance.",
    # app tab
    "Dil": "Language",
    "Dil:": "Language:",
    "Otomatik (sistem dili)": "Automatic (system language)",
    "Dil değişikliği için uygulama yeniden başlatılacak.": "The app will restart to change the language.",
    "Sistem tepsisi": "System tray",
    "Sistem tepsisinde simge göster": "Show an icon in the system tray",
    "Pencereyi kapatınca tepsiye küçült": "Minimize to tray when the window is closed",
    "Oturum açılınca tepside başlat": "Start in the tray when I log in",
    "Klavye kısayolu için komutlar": "Commands for keyboard shortcuts",
    "<code>victus-kbd --toggle</code> &nbsp;ışığı aç/kapat<br>"
    "<code>victus-kbd --profile Gece</code> &nbsp;profil seç<br>"
    "<code>victus-kbd --next-profile</code> &nbsp;sıradaki profil":
        "<code>victus-kbd --toggle</code> &nbsp;turn the light on/off<br>"
        "<code>victus-kbd --profile Night</code> &nbsp;apply a profile<br>"
        "<code>victus-kbd --next-profile</code> &nbsp;next profile",
    "KDE'de: Sistem Ayarları → Klavye → Kısayollar → Yeni Ekle → Komut veya Betik":
        "KDE: System Settings → Keyboard → Shortcuts → Add New → Command or Script",
    # status / dialogs
    "Kaydedilemedi": "Could not save",
    "Ayar dosyası yazılamadı:\n{error}": "Could not write the settings file:\n{error}",
    "✔ Kaydedildi": "✔ Saved",
    "⚠ Servis çalışmıyor ({state}): sudo systemctl start victus-kbd":
        "⚠ Service is not running ({state}): sudo systemctl start victus-kbd",
    "Değişiklikler klavyede önizleniyor; kalıcı olması için Kaydet'e basın.":
        "Changes are previewed on the keyboard; press Save to keep them.",
    "Servis çalışıyor, ayarlar kaydedildi.": "Service is running, settings are saved.",
    "Kaydedilmemiş değişiklikler": "Unsaved changes",
    "Değişiklikler kaydedilmedi. Kaydetmek ister misiniz?":
        "Your changes are not saved. Do you want to save them?",
    "Profil kaydet": "Save profile",
    "Profil adı:": "Profile name:",
    "Bu ad kullanılamaz.": "This name cannot be used.",
    "“{name}” zaten var. Üzerine yazılsın mı?": "“{name}” already exists. Overwrite it?",
    "Profili sil": "Delete profile",
    "“{name}” silinsin mi?": "Delete “{name}”?",
    "Renk seç": "Pick a color",
    "Rengi değiştir": "Change color",
    "En az bir renk kalmalı.": "At least one color must remain.",
    # preview strip
    "Kapalı": "Off",
    "Kapalı · pilde": "Off · on battery",
    "Kapalı · zamanlamada gün seçilmedi": "Off · no days selected in the schedule",
    "Kapalı · zamanlama dışında ({start}'da açılır)": "Off · outside the schedule (turns on at {start})",
    "Kapalı · parlaklık %0": "Off · brightness 0%",
    # tray
    "Ayarları aç": "Open settings",
    "Işık açık": "Light on",
    "Profiller": "Profiles",
    "Çıkış": "Quit",
    # command line
    "HP Victus klavye ışığı": "HP Victus keyboard lighting",
    "ışığı aç/kapat": "turn the light on/off",
    "ışığı aç": "turn the light on",
    "ışığı kapat": "turn the light off",
    "profili uygula": "apply a profile",
    "sıradaki profile geç": "switch to the next profile",
    "profilleri listele": "list profiles",
    "pencereyi açmadan tepside başlat": "start in the tray without opening the window",
    "sürümü göster": "show the version",
    "AD": "NAME",
    "Hiç profil yok.": "There are no profiles.",
    "Profil bulunamadı: {name}. Mevcut: {names}": "Profile not found: {name}. Available: {names}",
    "Ayar dosyası yazılamadı: {error}": "Could not write the settings file: {error}",
}


def T(text):
    """Translate a Turkish UI string for the current language."""
    if LANG == "tr":
        return text
    return EN.get(text, text)
