"""Persistent system-tray icon for COSMIC (StatusNotifierItem via AppIndicator).

Runs as a small background process (`cosmicshot tray`) and adds a crosshair icon
to the COSMIC panel with a capture menu -- the CleanShot menu-bar equivalent.
Each menu action launches a normal capture in its own process, so the tray daemon
stays simple and never nests GTK main loops.
"""
import os
import shutil
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config

# Prefer Ayatana (current); fall back to legacy libappindicator if present.
_AppIndicator = None
for _name in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_name, "0.1")
        _AppIndicator = __import__("gi.repository", fromlist=[_name]).__dict__[_name]
        break
    except (ValueError, ImportError, KeyError):
        continue

MENU = [
    ("Capture Region", ["region"]),
    ("Capture Screen…", ["screen"]),
    ("Capture App Window…", ["window"]),
    ("Scrolling Screenshot (Region)…", ["scroll", "--target", "region"]),
    ("Scrolling Screenshot (App Window)…", ["scroll", "--target", "window"]),
]


def _base_cmd():
    exe = shutil.which("cosmicshot")
    if exe:
        return [exe]
    return [sys.executable, "-m", "cosmicshot"]


# Delay (ms) between dismissing the menu and grabbing the screen. The grab
# (cosmic-screenshot) captures the compositor's current frame, so the menu must
# be fully unmapped first; an idle callback fires too early — before the
# compositor has repainted without the menu — leaving it visible in the shot.
_MENU_CLOSE_MS = 300


def _launch(args):
    subprocess.Popen(_base_cmd() + list(args), env=os.environ.copy())


def _build_menu():
    menu = Gtk.Menu()
    for label, args in MENU:
        item = Gtk.MenuItem(label=label)
        # Wait for the menu to actually disappear from the screen before the
        # capture grabs, otherwise the menu shows up in the screenshot.
        item.connect("activate",
                     lambda _w, a=args: GLib.timeout_add(
                         _MENU_CLOSE_MS, lambda: (_launch(a), False)[1]))
        menu.append(item)
    menu.append(Gtk.SeparatorMenuItem())
    quit_item = Gtk.MenuItem(label="Quit CosmicShot")
    quit_item.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    return menu


def run_tray(cfg=None):
    if _AppIndicator is None:
        raise RuntimeError(
            "No AppIndicator library found. Install gir1.2-ayatanaappindicator3-0.1 "
            "(or gir1.2-appindicator3-0.1).")
    # Single-instance: launching the app from the dock again must not stack a
    # second tray icon. Hold the lock for the life of the process.
    from .lock import SingleInstance
    tray_lock = SingleInstance("tray")
    if not tray_lock.acquire():
        print("cosmicshot: tray already running.")
        return 0
    run_tray._lock = tray_lock  # keep a reference so it isn't GC'd
    ind = _AppIndicator.Indicator.new(
        config.APP_ID, config.APP_ID,
        _AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    ind.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
    ind.set_title(config.APP_NAME)
    # Add the bundled icon's directory as a search path so the "cosmicshot" icon
    # name resolves even when the themed icon isn't installed.
    if os.path.exists(config.ICON_FILE):
        ind.set_icon_theme_path(os.path.dirname(config.ICON_FILE))
    ind.set_icon_full(config.APP_ID, config.APP_NAME)
    ind.set_menu(_build_menu())
    Gtk.main()
