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
    None,  # separator
    ("Record Region…", ["record", "--target", "region"]),
    ("Record App Window…", ["record", "--target", "window"]),
    ("Record Screen…", ["record", "--target", "screen"]),
]


def _base_cmd():
    exe = shutil.which("cosmicshot")
    if exe:
        return [exe]
    return [sys.executable, "-m", "cosmicshot"]


# Delay (ms) between dismissing the menu and grabbing the screen. The capture
# (cosmic-screenshot) grabs the compositor's current frame, and the panel menu
# is rendered by the COSMIC panel (not us), so we must force it closed AND wait
# for the panel to actually remove it from the screen — otherwise it lands in
# the screenshot.
_MENU_CLOSE_MS = 600


def _launch(args):
    subprocess.Popen(_base_cmd() + list(args), env=os.environ.copy())


def _on_activate(item, args):
    parent = item.get_parent()
    if parent is not None:
        try:
            parent.popdown()       # ask the menu to close right now
        except Exception:
            pass
    # …then wait for the panel to repaint without it before grabbing.
    GLib.timeout_add(_MENU_CLOSE_MS, lambda: (_launch(args), False)[1])


def _build_menu():
    menu = Gtk.Menu()
    for entry in MENU:
        if entry is None:
            menu.append(Gtk.SeparatorMenuItem())
            continue
        label, args = entry
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda w, a=args: _on_activate(w, a))
        menu.append(item)
    menu.append(Gtk.SeparatorMenuItem())
    quit_item = Gtk.MenuItem(label="Quit CosmicShot")
    quit_item.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    return menu


def _stop_recording(*_):
    """Tell the active recording (full-screen) to stop and save."""
    import signal
    from . import lock
    pid = lock.recording_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass


def _build_recording_menu():
    menu = Gtk.Menu()
    stop = Gtk.MenuItem(label="⏹  Stop recording")
    stop.connect("activate", _stop_recording)
    menu.append(stop)
    menu.append(Gtk.SeparatorMenuItem())
    quit_item = Gtk.MenuItem(label="Quit CosmicShot")
    quit_item.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    return menu


def _apply_state(ind):
    """Reflect whether a recording is in progress in the panel: a red Stop
    menu + "REC" label while recording, the normal capture menu otherwise."""
    from . import lock
    recording = lock.recording_pid() is not None
    if recording:
        ind.set_menu(_build_recording_menu())
        try:
            ind.set_label("● REC", "● REC")
        except Exception:
            pass
        ind.set_icon_full("media-record", "Recording")
    else:
        ind.set_menu(_build_menu())
        try:
            ind.set_label("", "")
        except Exception:
            pass
        ind.set_icon_full(config.APP_ID, config.APP_NAME)
    return False  # for GLib.unix_signal_add (stay registered via re-add below)


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

    # Let a full-screen recording hand its Stop button to the panel: publish our
    # PID and refresh the menu/icon whenever a recording signals us (SIGUSR1).
    import signal
    from . import lock
    lock.write_tray_pid()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                         lambda: (_apply_state(ind), True)[1])
    _apply_state(ind)  # in case a recording is already running

    Gtk.main()
