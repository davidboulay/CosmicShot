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
    ("Capture Region", ["region"], "applets-screenshooter-symbolic"),
    ("Capture Screen…", ["screen"], "video-display-symbolic"),
    ("Capture App Window…", ["window"], "focus-windows-symbolic"),
    ("Scrolling Screenshot (Region)…", ["scroll", "--target", "region"], "go-bottom-symbolic"),
    ("Scrolling Screenshot (App Window)…", ["scroll", "--target", "window"], "go-bottom-symbolic"),
    None,  # separator
    ("Record Region…", ["record", "--target", "region"], "media-record-symbolic"),
    ("Record App Window…", ["record", "--target", "window"], "media-record-symbolic"),
    ("Record Screen…", ["record", "--target", "screen"], "media-record-symbolic"),
]


def _base_cmd():
    exe = shutil.which("cosmicshot")
    if exe:
        return [exe]
    return [sys.executable, "-m", "cosmicshot"]


# Small settle after the click before launching; the launched process then
# actively dismisses the (panel-rendered) menu before grabbing — see
# overlay.dismiss_popups / COSMICSHOT_FROM_TRAY in app.main.
_MENU_CLOSE_MS = 120


def _launch(args):
    env = os.environ.copy()
    env["COSMICSHOT_FROM_TRAY"] = "1"          # capture dismisses the panel menu
    subprocess.Popen(_base_cmd() + list(args), env=env)


def _on_activate(item, args):
    parent = item.get_parent()
    if parent is not None:
        try:
            parent.popdown()
        except Exception:
            pass
    GLib.timeout_add(_MENU_CLOSE_MS, lambda: (_launch(args), False)[1])


def _menu_item(label, icon_name=None):
    """A menu item with an optional themed icon (icons are shown by panels that
    support dbusmenu icons; otherwise it's a plain text item)."""
    if icon_name:
        try:
            item = Gtk.ImageMenuItem.new_with_label(label)
            item.set_image(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU))
            item.set_always_show_image(True)
            return item
        except Exception:
            pass
    return Gtk.MenuItem(label=label)


def _build_menu():
    menu = Gtk.Menu()
    header = _menu_item(f"CosmicShot  ·  v{config.VERSION}", "applets-screenshooter-symbolic")
    header.set_sensitive(False)   # non-clickable version label
    menu.append(header)
    menu.append(Gtk.SeparatorMenuItem())
    for entry in MENU:
        if entry is None:
            menu.append(Gtk.SeparatorMenuItem())
            continue
        label, args, icon = entry
        item = _menu_item(label, icon)
        item.connect("activate", lambda w, a=args: _on_activate(w, a))
        menu.append(item)
    menu.append(Gtk.SeparatorMenuItem())
    settings_item = _menu_item("Settings…", "preferences-system-symbolic")
    # Settings is a window, not a capture — launch it directly (no menu-dismiss
    # dance needed).
    settings_item.connect("activate",
                          lambda *_: GLib.idle_add(lambda: (_launch(["settings"]), False)[1]))
    menu.append(settings_item)
    quit_item = _menu_item("Quit CosmicShot", "application-exit-symbolic")
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
            ind.set_label("REC", "REC")
        except Exception:
            pass
        # Red ⏹ stop button (bundled icon, resolved via the theme search path).
        if os.path.exists(config.STOP_ICON_FILE):
            ind.set_icon_theme_path(os.path.dirname(config.STOP_ICON_FILE))
        ind.set_icon_full(config.STOP_ICON_NAME, "Stop recording")
    else:
        ind.set_menu(_build_menu())
        try:
            ind.set_label("", "")
        except Exception:
            pass
        if os.path.exists(config.ICON_FILE):
            ind.set_icon_theme_path(os.path.dirname(config.ICON_FILE))
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

    # Auto-update: check shortly after launch, then hourly, and prompt the user
    # directly with a one-click Install dialog (no Settings trip needed).
    GLib.timeout_add_seconds(10, lambda: (_check_updates(), False)[1])
    GLib.timeout_add_seconds(3600, lambda: (_check_updates(), True)[1])

    Gtk.main()


def _check_updates():
    """If auto-update is on, check GitHub in the background and prompt when a
    newer release is available."""
    if not config.load().get("auto_update"):
        return
    import threading

    def work():
        try:
            from . import updates
            info = updates.available()
        except Exception:
            return
        if info:
            GLib.idle_add(lambda: (_prompt_update(info), False)[1])

    threading.Thread(target=work, daemon=True).start()


def _prompt_update(info):
    """Show an explicit Install / Later dialog — once per version."""
    from . import config as _cfg
    cfg = _cfg.load()
    if cfg.get("update_prompted_version") == info["version"]:
        return  # already asked about this one; don't nag every hour
    cfg["update_prompted_version"] = info["version"]
    _cfg.save(cfg)

    dlg = Gtk.MessageDialog(
        transient_for=None, modal=False, message_type=Gtk.MessageType.INFO,
        text=f"CosmicShot {info['version']} is available")
    dlg.format_secondary_text(
        f"You're on {config.VERSION}. Install the update now? "
        "You'll be asked for your password, then the app restarts.")
    dlg.add_button("Later", Gtk.ResponseType.CANCEL)
    inst = dlg.add_button("Install now", Gtk.ResponseType.ACCEPT)
    inst.get_style_context().add_class("suggested-action")
    dlg.set_default_response(Gtk.ResponseType.ACCEPT)
    dlg.set_keep_above(True)
    dlg.connect("response", _on_prompt_response, info)
    dlg.show()


def _on_prompt_response(dlg, resp, info):
    dlg.destroy()
    if resp == Gtk.ResponseType.ACCEPT:
        _install_update(info)


def _install_update(info):
    import threading
    from . import updates, export

    def work():
        path = updates.download_deb(info.get("deb_url"))
        if not path:
            GLib.idle_add(lambda: (export.notify(
                "CosmicShot", "Couldn't download the update."), False)[1])
            return
        ok, msg = updates.install_deb(path)   # pkexec password prompt
        if ok:
            GLib.idle_add(lambda: (updates.relaunch(), False)[1])  # re-exec new tray
        else:
            GLib.idle_add(lambda: (export.notify(
                "CosmicShot — update failed", msg or ""), False)[1])

    threading.Thread(target=work, daemon=True).start()
