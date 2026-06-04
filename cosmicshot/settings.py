"""CosmicShot settings window: version + updates + global keyboard shortcuts."""
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from . import config, updates, shortcuts, export


def _capture_accel(parent):
    """Modal grab: return an accelerator string like 'Super+Shift+S', or None."""
    dlg = Gtk.Dialog(title="Set shortcut", transient_for=parent, modal=True)
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    box = dlg.get_content_area()
    box.set_margin_top(18); box.set_margin_bottom(18)
    box.set_margin_start(24); box.set_margin_end(24)
    box.add(Gtk.Label(label="Press the key combination…\n(Esc to cancel)"))
    result = {"accel": None}
    _MODS = {"Super_L", "Super_R", "Shift_L", "Shift_R", "Control_L",
             "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R", "ISO_Level3_Shift"}

    def on_key(_w, ev):
        name = Gdk.keyval_name(ev.keyval) or ""
        if name == "Escape":
            dlg.response(Gtk.ResponseType.CANCEL)
            return True
        if name in _MODS:
            return True  # wait for a real key
        s = ev.state
        mods = []
        if s & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK):
            mods.append("Super")
        if s & Gdk.ModifierType.CONTROL_MASK:
            mods.append("Ctrl")
        if s & Gdk.ModifierType.MOD1_MASK:
            mods.append("Alt")
        if s & Gdk.ModifierType.SHIFT_MASK:
            mods.append("Shift")
        key = name.upper() if (len(name) == 1 and name.isalpha()) else name
        result["accel"] = "+".join(mods + [key])
        dlg.response(Gtk.ResponseType.OK)
        return True

    dlg.connect("key-press-event", on_key)
    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    return result["accel"] if resp == Gtk.ResponseType.OK else None


class SettingsWindow:
    def __init__(self, cfg=None):
        self.cfg = cfg or config.load()
        self._latest = None  # cached update info
        self.win = Gtk.Window(title="CosmicShot Settings")
        self.win.set_default_size(520, 0)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.set_border_width(18)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.win.add(root)
        root.pack_start(self._version_section(), False, False, 0)
        root.pack_start(Gtk.Separator(), False, False, 0)
        root.pack_start(self._shortcuts_section(), False, False, 0)

    # -- version / updates ----------------------------------------------
    def _version_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>CosmicShot</b>  ·  version {config.VERSION}")
        box.pack_start(title, False, False, 0)

        self._status = Gtk.Label(xalign=0, label="")
        self._status.get_style_context().add_class("dim-label")
        box.pack_start(self._status, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._check_btn = Gtk.Button(label="Check for updates")
        self._check_btn.connect("clicked", lambda _b: self._check(manual=True))
        row.pack_start(self._check_btn, False, False, 0)
        self._update_btn = Gtk.Button(label="Update now")
        self._update_btn.get_style_context().add_class("suggested-action")
        self._update_btn.connect("clicked", lambda _b: self._do_update())
        self._update_btn.set_no_show_all(True)
        self._update_btn.hide()
        row.pack_start(self._update_btn, False, False, 0)
        box.pack_start(row, False, False, 0)

        self._auto = Gtk.CheckButton(label="Automatically check for updates")
        self._auto.set_active(bool(self.cfg.get("auto_update")))
        self._auto.connect("toggled", self._on_auto_toggled)
        box.pack_start(self._auto, False, False, 0)
        return box

    def _on_auto_toggled(self, btn):
        self.cfg["auto_update"] = btn.get_active()
        config.save(self.cfg)

    def _check(self, manual=False):
        self._status.set_text("Checking for updates…")
        self._check_btn.set_sensitive(False)

        def work():
            info = updates.check_latest()
            GLib.idle_add(self._check_done, info, manual)

        threading.Thread(target=work, daemon=True).start()

    def _check_done(self, info, manual):
        self._check_btn.set_sensitive(True)
        if not info:
            self._status.set_text("Couldn't check for updates (offline?).")
            return False
        if updates.is_newer(info["tag"]):
            self._latest = info
            self._status.set_markup(
                f"<b>Update available:</b> {info['version']} "
                f"(you have {config.VERSION})")
            self._update_btn.show()
        else:
            self._status.set_text(f"You're up to date ({config.VERSION}).")
            self._update_btn.hide()
        return False

    def _do_update(self):
        if not self._latest or not self._latest.get("deb_url"):
            self._status.set_text("No installable package found for the release.")
            return
        self._status.set_text("Downloading update…")
        self._update_btn.set_sensitive(False)

        def work():
            path = updates.download_deb(self._latest["deb_url"])
            GLib.idle_add(lambda: self._status.set_text(
                "Installing (enter your password)…") or False)
            ok = updates.install_deb(path) if path else False
            GLib.idle_add(self._update_done, ok)

        threading.Thread(target=work, daemon=True).start()

    def _update_done(self, ok):
        self._update_btn.set_sensitive(True)
        if ok:
            self._status.set_markup("<b>Updated.</b> Restart CosmicShot to use the new version.")
            self._update_btn.hide()
            export.notify("CosmicShot", "Update installed — restart to use it.")
        else:
            self._status.set_text("Update failed (cancelled or no .deb install).")
        return False

    # -- shortcuts -------------------------------------------------------
    def _shortcuts_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Global keyboard shortcuts</b>")
        box.pack_start(title, False, False, 0)
        hint = Gtk.Label(xalign=0, label="Empty by default. Set a combination to "
                         "register it system-wide via COSMIC.")
        hint.get_style_context().add_class("dim-label")
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self._accel_labels = {}
        sc = dict(self.cfg.get("shortcuts") or {})
        for i, (aid, label, _cmd) in enumerate(config.SHORTCUT_ACTIONS):
            name = Gtk.Label(label=label, xalign=0, hexpand=True)
            grid.attach(name, 0, i, 1, 1)
            cur = Gtk.Label(label=sc.get(aid) or "—")
            cur.get_style_context().add_class("dim-label")
            cur.set_width_chars(16)
            self._accel_labels[aid] = cur
            grid.attach(cur, 1, i, 1, 1)
            setb = Gtk.Button(label="Set…")
            setb.connect("clicked", lambda _b, a=aid: self._set_shortcut(a))
            grid.attach(setb, 2, i, 1, 1)
            clrb = Gtk.Button(label="Clear")
            clrb.connect("clicked", lambda _b, a=aid: self._set_shortcut(a, clear=True))
            grid.attach(clrb, 3, i, 1, 1)
        box.pack_start(grid, False, False, 0)

        self._sc_note = Gtk.Label(xalign=0, label="")
        self._sc_note.get_style_context().add_class("dim-label")
        box.pack_start(self._sc_note, False, False, 0)
        return box

    def _set_shortcut(self, action_id, clear=False):
        accel = None if clear else _capture_accel(self.win)
        if not clear and accel is None:
            return  # cancelled
        sc = dict(self.cfg.get("shortcuts") or {})
        if clear:
            sc.pop(action_id, None)
        else:
            sc[action_id] = accel
        self.cfg["shortcuts"] = sc
        config.save(self.cfg)
        self._accel_labels[action_id].set_text(accel or "—")
        try:
            changed = shortcuts.apply(sc)
            self._sc_note.set_text(
                "Saved to COSMIC. If it doesn't work right away, log out and back in."
                if changed else "Saved.")
        except Exception as exc:
            self._sc_note.set_text(f"Couldn't write COSMIC shortcuts: {exc}")

    # -- lifecycle -------------------------------------------------------
    def run(self):
        self.win.connect("destroy", Gtk.main_quit)
        self.win.show_all()
        self._update_btn.hide()
        if self.cfg.get("auto_update"):
            self._check()
        Gtk.main()
