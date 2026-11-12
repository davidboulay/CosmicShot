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
            # Record the unshifted key ('4', not 'dollar'): COSMIC matches
            # shortcuts on it (issue #7). Keep NumLock so keypad keys stay KP_n.
            name = shortcuts.base_keyname(
                ev.hardware_keycode, s & Gdk.ModifierType.MOD2_MASK, ev.group) or name
        result["accel"] = shortcuts.format_accel(mods, name)
        dlg.response(Gtk.ResponseType.OK)
        return True

    dlg.connect("key-press-event", on_key)
    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    return result["accel"] if resp == Gtk.ResponseType.OK else None


def _card(rows):
    """An inset grouped list: rounded card, rows split by hairlines."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.get_style_context().add_class("cs-card")
    for i, row in enumerate(rows):
        if i:
            sep = Gtk.Box()
            sep.get_style_context().add_class("cs-card-sep")
            card.pack_start(sep, False, False, 0)
            row._cs_sep = sep
        card.pack_start(row, False, False, 0)
    return card


def _row(title, control=None, subtitle=None):
    """Label on the left (optional dim subtitle under it), control on the right."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.get_style_context().add_class("cs-card-row")
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    text.set_valign(Gtk.Align.CENTER)
    lab = Gtk.Label(label=title, xalign=0)
    text.pack_start(lab, False, False, 0)
    if subtitle:
        sub = Gtk.Label(label=subtitle, xalign=0)
        sub.get_style_context().add_class("cs-hero-sub")
        sub.set_line_wrap(True)
        text.pack_start(sub, False, False, 0)
    row.pack_start(text, True, True, 0)
    if control is not None:
        control.set_valign(Gtk.Align.CENTER)
        row.pack_end(control, False, False, 0)
    return row


def _section(title, card, footer=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    if title:
        t = Gtk.Label(label=title.upper(), xalign=0)
        t.get_style_context().add_class("cs-section-title")
        box.pack_start(t, False, False, 0)
    box.pack_start(card, False, False, 0)
    if footer is not None:
        box.pack_start(footer, False, False, 0)
    return box


def _footer(text=""):
    lab = Gtk.Label(label=text, xalign=0)
    lab.get_style_context().add_class("cs-footer")
    lab.set_line_wrap(True)
    lab.set_max_width_chars(60)
    return lab


def _button(label, style="cs-secondary"):
    b = Gtk.Button(label=label)
    b.get_style_context().add_class(style)
    return b


class SettingsWindow:
    def __init__(self, cfg=None):
        from . import theme
        self.cfg = cfg or config.load()
        self._latest = None  # cached update info
        theme.install(theme.is_dark())
        self.win = Gtk.Window(title="CosmicShot Settings")
        self.win.get_style_context().add_class("cs-window")
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.set_default_size(560, 680)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Settings"
        self.win.set_titlebar(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(760)
        self.win.add(scroller)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        root.get_style_context().add_class("cs-settings")
        root.get_style_context().add_class("cs-root")
        scroller.add(root)
        root.pack_start(self._version_section(), False, False, 0)
        root.pack_start(self._shortcuts_section(), False, False, 0)
        root.pack_start(self._upload_section(), False, False, 0)

    # -- version / updates ----------------------------------------------
    def _version_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = config.icon_path()
        if icon:
            try:
                from gi.repository import GdkPixbuf
                sc = self.win.get_scale_factor()
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon, 64 * sc, 64 * sc)
                surf = Gdk.cairo_surface_create_from_pixbuf(pb, sc, None)
                hero.pack_start(Gtk.Image.new_from_surface(surf), False, False, 0)
            except Exception:
                pass
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=config.APP_NAME, xalign=0)
        name.get_style_context().add_class("cs-hero-name")
        text.pack_start(name, False, False, 0)
        ver = Gtk.Label(label=f"Version {config.VERSION}", xalign=0)
        ver.get_style_context().add_class("cs-hero-sub")
        text.pack_start(ver, False, False, 0)
        self._status = Gtk.Label(xalign=0, label="")
        self._status.get_style_context().add_class("cs-hero-sub")
        self._status.set_line_wrap(True)
        text.pack_start(self._status, False, False, 0)
        hero.pack_start(text, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        btns.set_valign(Gtk.Align.CENTER)
        self._update_btn = _button("Update Now", "cs-primary")
        self._update_btn.connect("clicked", lambda _b: self._do_update())
        self._update_btn.set_no_show_all(True)
        btns.pack_start(self._update_btn, False, False, 0)
        self._check_btn = _button("Check for Updates")
        self._check_btn.connect("clicked", lambda _b: self._check(manual=True))
        btns.pack_start(self._check_btn, False, False, 0)
        hero.pack_end(btns, False, False, 0)
        box.pack_start(hero, False, False, 0)

        self._auto = Gtk.Switch()
        self._auto.set_active(bool(self.cfg.get("auto_update")))
        self._auto.connect("notify::active", lambda sw, _p: self._on_auto_toggled(sw))
        box.pack_start(_section("Updates", _card([
            _row("Check for updates automatically", self._auto,
                 "On launch and every hour; you're asked before anything installs.")])),
            False, False, 0)
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
                f"<b>Version {GLib.markup_escape_text(info['version'])} is available.</b>")
            self._update_btn.show()
        else:
            self._status.set_text("CosmicShot is up to date.")
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
            if path:
                ok, msg = updates.install_deb(path)
            else:
                ok, msg = False, "Could not download the update."
            GLib.idle_add(self._update_done, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _update_done(self, ok, msg=None):
        if ok:
            ver = (self._latest or {}).get("version", "")
            self._status.set_markup("<b>Update installed — restarting…</b>")
            self._update_btn.hide()
            export.notify("CosmicShot", f"Updated to {ver} — restarting…".strip())
            # brief feedback, then re-exec into the new version (briefing §8)
            GLib.timeout_add(1200, lambda: (updates.relaunch(), False)[1])
        else:
            self._update_btn.set_sensitive(True)
            self._status.set_text(f"Update failed: {msg or 'unknown error'}")
        return False

    # -- shortcuts -------------------------------------------------------
    def _shortcuts_section(self):
        from . import capture
        self._plasma = capture.is_plasma()
        self._accel_labels = {}
        self._clear_btns = {}
        sc = dict(self.cfg.get("shortcuts") or {})
        rows = []
        for aid, label, _cmd in config.SHORTCUT_ACTIONS:
            ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cur = Gtk.Label()
            cur.get_style_context().add_class("cs-keycap")
            self._accel_labels[aid] = cur
            ctl.pack_start(cur, False, False, 0)
            setb = _button("Record", "cs-flat")
            setb.set_tooltip_text("Press a new key combination")
            setb.connect("clicked", lambda _b, a=aid: self._set_shortcut(a))
            ctl.pack_start(setb, False, False, 0)
            clrb = Gtk.Button()
            clrb.set_image(Gtk.Image.new_from_icon_name("edit-clear-symbolic",
                                                        Gtk.IconSize.MENU))
            clrb.get_style_context().add_class("cs-flat")
            clrb.set_tooltip_text("Remove this shortcut")
            clrb.connect("clicked", lambda _b, a=aid: self._set_shortcut(a, clear=True))
            self._clear_btns[aid] = clrb
            ctl.pack_start(clrb, False, False, 0)
            self._show_accel(aid, sc.get(aid))
            rows.append(_row(label, ctl))
        if self._plasma:
            hint = ("On KDE Plasma, global shortcuts are set in System Settings › "
                    "Keyboard › Shortcuts › Add New › Command — use the command "
                    "shown in each row's tooltip. The combinations recorded here "
                    "only apply on COSMIC.")
            for row, (_aid, _label, cmd) in zip(rows, config.SHORTCUT_ACTIONS):
                row.set_tooltip_text(cmd)
        else:
            hint = "Empty by default. Recorded combinations are registered system-wide via COSMIC."
        self._sc_note = _footer(hint)
        return _section("Keyboard Shortcuts", _card(rows), self._sc_note)

    def _show_accel(self, aid, accel):
        lab = self._accel_labels[aid]
        ctx = lab.get_style_context()
        if accel:
            lab.set_text(accel.replace("+", " + "))
            ctx.remove_class("empty")
        else:
            lab.set_text("Not set")
            ctx.add_class("empty")
        self._clear_btns[aid].set_sensitive(bool(accel))

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
        self._show_accel(action_id, accel)
        try:
            changed = shortcuts.apply(sc)
            self._sc_note.set_text(
                "Saved to COSMIC. If it doesn't work right away, log out and back in."
                if changed else "Saved.")
        except Exception as exc:
            self._sc_note.set_text(f"Couldn't write COSMIC shortcuts: {exc}")

    # -- upload ----------------------------------------------------------
    def _upload_section(self):
        """Cloud upload: on/off, host preset, or a custom endpoint (issue #4)."""
        self._upload_enabled = Gtk.Switch()
        self._upload_enabled.set_active(bool(self.cfg.get("upload_enabled", True)))
        self._upload_enabled.connect("notify::active",
                                     lambda sw, _p: self._on_upload_toggled(sw))

        self._upload_host = Gtk.ComboBoxText()
        for label, _url, _field, _extra in config.UPLOAD_PRESETS:
            self._upload_host.append_text(label)
        self._upload_host.append_text("Custom…")

        self._upload_url = Gtk.Entry(hexpand=True)
        self._upload_url.set_placeholder_text("https://example.com/upload")
        self._upload_url.set_tooltip_text("Endpoint that accepts a multipart POST and "
                                          "answers with the URL (plain text or JSON).")
        self._upload_field = Gtk.Entry(hexpand=True)
        self._upload_field.set_placeholder_text("file")
        self._upload_field.set_tooltip_text("Name of the multipart file field.")
        self._upload_extra = Gtk.Entry(hexpand=True)
        self._upload_extra.set_placeholder_text('{"reqtype": "fileupload"}')
        self._upload_extra.set_tooltip_text("Extra form fields, as a JSON object.")

        rows = [_row("Show the Upload button", self._upload_enabled),
                _row("Host", self._upload_host)]
        self._custom_rows = []
        for label, entry in [("Endpoint URL", self._upload_url),
                             ("File field", self._upload_field),
                             ("Extra fields", self._upload_extra)]:
            entry.set_width_chars(26)
            r = _row(label, entry)
            rows.append(r)
            self._custom_rows.append(r)
            entry.connect("changed", self._on_upload_custom_changed)
        card = _card(rows)

        self._upload_note = _footer("Uploads are public: anyone with the link can view "
                                    "the image. Redact with Blur or Spotlight first.")

        # Select the preset matching the saved endpoint, else "Custom…".
        url = self.cfg.get("upload_service") or ""
        idx = next((i for i, p in enumerate(config.UPLOAD_PRESETS) if p[1] == url),
                   len(config.UPLOAD_PRESETS))
        self._upload_url.set_text(url)
        self._upload_field.set_text(self.cfg.get("upload_field") or "")
        import json
        self._upload_extra.set_text(json.dumps(self.cfg.get("upload_extra") or {}))
        self._upload_host.set_active(idx)
        self._upload_host.connect("changed", self._on_upload_host_changed)
        return _section("Cloud Upload", card, self._upload_note)

    def _sync_upload_rows(self):
        enabled = self._upload_enabled.get_active()
        custom = self._upload_host.get_active() >= len(config.UPLOAD_PRESETS)
        self._upload_host.set_sensitive(enabled)
        for row in self._custom_rows:
            for w in (row, getattr(row, "_cs_sep", None)):
                if w is None:
                    continue
                w.set_no_show_all(False)
                if custom:
                    w.show_all()
                else:
                    w.hide()
                w.set_no_show_all(True)
            row.set_sensitive(enabled)

    def _on_upload_toggled(self, btn):
        self.cfg["upload_enabled"] = btn.get_active()
        config.save(self.cfg)
        self._sync_upload_rows()
        self._upload_note.set_text("Saved. Takes effect on the next capture.")

    def _on_upload_host_changed(self, combo):
        i = combo.get_active()
        if 0 <= i < len(config.UPLOAD_PRESETS):
            _label, url, field, extra = config.UPLOAD_PRESETS[i]
            import json
            self._upload_url.set_text(url)
            self._upload_field.set_text(field)
            self._upload_extra.set_text(json.dumps(extra))
            self.cfg.update(upload_service=url, upload_field=field, upload_extra=dict(extra))
            config.save(self.cfg)
            self._upload_note.set_text("Saved. Takes effect on the next capture.")
        self._sync_upload_rows()

    def _on_upload_custom_changed(self, _entry):
        if self._upload_host.get_active() < len(config.UPLOAD_PRESETS):
            return  # preset values are being filled in programmatically
        import json
        url = self._upload_url.get_text().strip()
        field = self._upload_field.get_text().strip() or "file"
        try:
            extra = json.loads(self._upload_extra.get_text() or "{}")
            if not isinstance(extra, dict):
                raise ValueError("must be a JSON object")
        except ValueError as exc:
            self._upload_note.set_text(f"Extra fields: not valid JSON ({exc}); not saved.")
            return
        if not url.startswith(("http://", "https://")):
            self._upload_note.set_text("Endpoint URL must start with http:// or https://; not saved.")
            return
        self.cfg.update(upload_service=url, upload_field=field, upload_extra=extra)
        config.save(self.cfg)
        self._upload_note.set_text("Saved. Takes effect on the next capture.")

    # -- lifecycle -------------------------------------------------------
    def run(self):
        self.win.connect("destroy", Gtk.main_quit)
        self.win.show_all()
        self._after_show()
        if self.cfg.get("auto_update"):
            self._check()
        Gtk.main()

    def _after_show(self):
        self._update_btn.hide()
        self._sync_upload_rows()
