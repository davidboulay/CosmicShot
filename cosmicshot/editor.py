"""The annotation editor window -- the heart of the CleanShot-style experience."""
import copy
import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402
import cairo  # noqa: E402

from . import config, export, tools, icons
from . import theme as theme_mod
from .imaging import pil_to_surface, make_pixelated

TOOLS = [
    ("select",    "Select / move / resize (V)", "✋"),
    ("arrow",     "Arrow (A)",       "↗"),
    ("rect",      "Rectangle (R)",   "▭"),
    ("ellipse",   "Ellipse (E)",     "◯"),
    ("line",      "Line (L)",        "╱"),
    ("pen",       "Pen (P)",         "✎"),
    ("highlight", "Highlighter (H)", "▰"),
    ("text",      "Text (T)",        "T"),
    ("counter",   "Step number (N)", "①"),
    ("blur",      "Blur / pixelate (B)", "▒"),
    ("spotlight", "Spotlight / focus (O)", "◉"),
    ("crop",      "Crop (X)",        "⛶"),
]

ACCENT = (0.0, 0.48, 1.0)
_BOX_HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"]

# Crop aspect ratios offered in the toolbar, as (label, width / height).
# Both orientations are listed so a ratio always means exactly what it says —
# picking "16:9" never silently gives you 9:16 because you dragged tall.
CROP_RATIOS = [
    ("Free", None), ("1:1", 1.0),
    ("4:3", 4 / 3), ("3:4", 3 / 4),
    ("3:2", 3 / 2), ("2:3", 2 / 3),
    ("16:9", 16 / 9), ("9:16", 9 / 16),
]

def install_theme(dark):
    """(Re)install the shared CosmicShot stylesheet for light/dark."""
    theme_mod.install(dark)


def _available_fonts():
    """A short, curated list of clean modern families that are actually
    installed, plus the generic aliases (always resolvable)."""
    candidates = ["Sans", "Ubuntu", "Open Sans", "Noto Sans", "Fira Sans",
                  "Cantarell", "Inter", "Roboto", "Montserrat", "Poppins",
                  "Lato", "Work Sans", "DejaVu Sans", "Serif", "Monospace"]
    have = set()
    try:
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        have = {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}
    except Exception:
        pass
    out = []
    for c in candidates:
        if c in ("Sans", "Serif", "Monospace") or c in have:
            out.append(c)
    return out


def _box_handle_points(bbox):
    x, y, w, h = bbox
    return {
        "nw": (x, y), "n": (x + w / 2, y), "ne": (x + w, y),
        "e": (x + w, y + h / 2), "se": (x + w, y + h),
        "s": (x + w / 2, y + h), "sw": (x, y + h), "w": (x, y + h / 2),
    }


def _resize_bbox(bbox, handle, nx, ny):
    """Return a new bbox after dragging `handle` to (nx, ny)."""
    x, y, w, h = bbox
    l, t, r, b = x, y, x + w, y + h
    if "n" in handle: t = ny
    if "s" in handle: b = ny
    if "w" in handle: l = nx
    if "e" in handle: r = nx
    nl, nr = min(l, r), max(l, r)
    nt, nb = min(t, b), max(t, b)
    return (nl, nt, max(1, nr - nl), max(1, nb - nt))


class _DrawCtx:
    def __init__(self, blur_surface, img_w=0, img_h=0):
        self.blur_surface = blur_surface
        self.img_w = img_w
        self.img_h = img_h


class _ColorWell(Gtk.Button):
    """The custom-colour control: a rainbow ring around the current colour
    (the familiar 'more colours' affordance) that opens the colour chooser.
    Mirrors Gtk.ColorButton's small API (get/set_rgba, "color-set")."""
    from gi.repository import GObject as _GObject
    __gsignals__ = {"color-set": (_GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, editor, hexc):
        super().__init__()
        self._editor = editor
        self._rgba = Gdk.RGBA(); self._rgba.parse(hexc)
        self._area = Gtk.DrawingArea()
        self._area.set_size_request(20, 20)
        self._area.connect("draw", self._draw)
        self.add(self._area)
        self.connect("clicked", self._choose)

    def get_rgba(self):
        return self._rgba

    def set_rgba(self, rgba):
        self._rgba = rgba
        self._area.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        cx, cy = a.width / 2, a.height / 2
        r = min(cx, cy)
        steps = 36
        for i in range(steps):                      # hue ring
            import colorsys
            rr, gg, bb = colorsys.hsv_to_rgb(i / steps, 0.85, 1.0)
            cr.set_source_rgb(rr, gg, bb)
            a0 = (i / steps) * 2 * math.pi - math.pi / 2
            a1 = ((i + 1.15) / steps) * 2 * math.pi - math.pi / 2
            cr.move_to(cx, cy)
            cr.arc(cx, cy, r, a0, a1)
            cr.close_path()
            cr.fill()
        bg = self._editor._pal["toolbar"].lstrip("#")
        cr.set_source_rgb(*(int(bg[i:i + 2], 16) / 255 for i in (0, 2, 4)))
        cr.arc(cx, cy, r - 2.6, 0, 2 * math.pi)
        cr.fill()
        c = self._rgba
        cr.set_source_rgb(c.red, c.green, c.blue)
        cr.arc(cx, cy, r - 4.4, 0, 2 * math.pi)
        cr.fill()
        return False

    def _choose(self, *_):
        dlg = Gtk.ColorChooserDialog(title="Custom colour",
                                     transient_for=self.get_toplevel(), modal=True)
        dlg.set_use_alpha(False)
        dlg.set_rgba(self._rgba)
        if dlg.run() == Gtk.ResponseType.OK:
            self.set_rgba(dlg.get_rgba())
            self.emit("color-set")
        dlg.destroy()


class Editor(Gtk.Window):
    def __init__(self, pil_image, cfg=None):
        super().__init__(title="CosmicShot")
        self.cfg = cfg or config.load()
        self.base_image = pil_image  # PIL image, replaced on crop
        self.annotations = []
        self.undo_stack = []
        self.redo_stack = []
        self.counter_value = 1

        self.tool = "arrow"
        self.color = self.cfg["default_color"]
        self.width = float(self.cfg["default_width"])
        self.font_size = float(self.cfg["default_font_size"])
        self.text_font = self.cfg.get("default_font", "Sans")
        self.blur_block = int(self.cfg.get("pixelate_block", 12))
        self.spotlight_darkness = float(self.cfg.get("spotlight_darkness", 0.6))

        self._dark = theme_mod.is_dark()
        self._pal = theme_mod.palette(self._dark)
        self._canvas_bg = self._pal["canvas"]

        self._zoom = 1.0          # 1.0 = fit-to-window; >1 zooms in
        from . import capture as _capture
        _s = _capture.display_scale()
        self._native_cap = (1.0 / _s) if _s else None  # Plasma only
        self._panx = 0.0          # extra pan offset (widget px) when zoomed
        self._pany = 0.0
        self.draft = None        # in-progress annotation (live preview)
        self.press_img = None     # press point in image coords
        self.crop_rect = None     # (x, y, w, h) image coords while crop tool active
        self.editing_text = None   # Text annotation being typed in-place
        self._caret_on = True
        self._caret_src = None
        self._caret = 0            # caret position (character index) while editing
        self._sel = None           # selection anchor (character index) or None
        self._text_drag = False    # dragging inside a text box to select a range
        self._edit_snapshot = None     # state captured when text editing began
        self._edit_undo_pushed = False
        self._maybe_edit = None        # text under a press, to edit on click-no-drag
        self._maybe_edit_pt = None     # where that press landed (to seat the caret)
        self._press_moved = False
        self.text_align = "left"
        self.pending_pin = None

        # Shift-constrained angles for lines/arrows, and straight highlighter
        # strokes. Both default ON; the toolbar checkboxes hold the setting and
        # Shift always inverts whatever is set.
        self.snap_angles = bool(self.cfg.get("angle_snap_lock", True))
        self.highlight_straight = bool(self.cfg.get("highlight_straight", True))
        self.circle_lock = bool(self.cfg.get("ellipse_circle_lock", False))
        self.crop_ratio = dict(CROP_RATIOS).get(self.cfg.get("crop_ratio", "Free"))

        # In-editor clipboard for annotation elements (Ctrl+C / Ctrl+X / Ctrl+V).
        self._ann_clip = None
        self._paste_n = 0

        # Select-tool state
        self.selected = None
        self.hover_ann = None
        self.active_handle = None
        self._moving = False
        self._drag_last = None
        self._predrag = None
        self._drag_committed = False

        self.dirty = False       # unsaved annotations/crop -> confirm on close
        self._closing = False    # set when intentionally closing (copy/save/pin/discard)

        # surfaces derived from base_image
        self._base_buf = self._blur_buf = None
        self.base_surface = None
        self.blur_surface = None
        self._rebuild_surfaces()

        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", self.on_delete_event)
        self.connect("destroy", lambda *_: Gtk.main_quit())

        self._build_ui()
        self._apply_window_sizing()

    # Chrome around the canvas (header bar + toolbar), and breathing room
    # kept around the image when the window is sized to fit it.
    _CHROME_H = 100
    _IMAGE_MARGIN = 48
    _MIN_W, _MIN_H = 480, 360

    def _target_workarea(self):
        """Workarea (logical px) of the screen the editor will open on.

        Wayland gives a client no say over placement and no way to ask where
        it will land, so guess the screen the compositor will pick: the active
        output on Plasma (KWin places new windows there), else the screen under
        the pointer, else the primary one. ``_fit_to_monitor`` corrects the
        size after mapping if the guess was wrong."""
        display = self.get_display() or Gdk.Display.get_default()
        mon = None
        try:
            from . import capture as _capture
            if _capture.is_plasma():
                pos = _capture._kwin_active_output_pos()
                if pos:
                    mon = display.get_monitor_at_point(int(pos[0]) + 1, int(pos[1]) + 1)
        except Exception:
            mon = None
        if mon is None:
            try:
                _s, x, y = display.get_default_seat().get_pointer().get_position()
                mon = display.get_monitor_at_point(x, y)
            except Exception:
                mon = None
        mon = mon or display.get_primary_monitor() or display.get_monitor(0)
        return mon.get_workarea() if mon else None

    def _ideal_size(self, wa):
        """Window size that shows the image at its natural size (1 image px per
        device px, like the capture) plus margins, capped to 92% x 90% of the
        workarea, and never narrower than the toolbar needs (when that fits)."""
        dev = self._device_scale()
        iw = self.base_image.width / dev
        ih = self.base_image.height / dev
        w = iw + 2 * self._IMAGE_MARGIN
        h = ih + 2 * self._IMAGE_MARGIN + self._CHROME_H
        # Measure the toolbar with its permanent widgets shown (contextual
        # controls stay hidden thanks to no_show_all) plus room for the widest
        # usual contextual control, so switching tools doesn't need scrolling.
        self.toolbar.show_all()
        tb = self.toolbar_content.get_preferred_width()[1] + 24 + 190
        w = max(w, tb, 640)
        h = max(h, 440)
        if wa is not None:
            w = min(w, int(wa.width * 0.92))
            h = min(h, int(wa.height * 0.90))
        return int(max(self._MIN_W, w)), int(max(self._MIN_H, h))

    def _apply_window_sizing(self):
        """Size the window to the image, within the target screen's workarea.

        The toolbar sits in a horizontal scroller, so it never forces the
        window wider than the screen; the minimum size stays small."""
        self.set_default_size(*self._ideal_size(self._target_workarea()))
        geom = Gdk.Geometry()
        geom.min_width, geom.min_height = self._MIN_W, self._MIN_H
        self.set_geometry_hints(None, geom, Gdk.WindowHints.MIN_SIZE)

    def _fit_to_monitor(self, *_):
        """After mapping, shrink the window if it ended up larger than the
        workarea of the screen it was actually placed on."""
        try:
            gdkwin = self.get_window()
            mon = self.get_display().get_monitor_at_window(gdkwin) if gdkwin else None
            if mon is None:
                return False
            wa = mon.get_workarea()
            w, h = self.get_size()
            mw, mh = int(wa.width * 0.96), int(wa.height * 0.94)
            if w > mw or h > mh:
                self.resize(min(w, mw), min(h, mh))
        except Exception:
            pass
        return False

    def _device_scale(self):
        """Device px per logical px: Plasma's fractional scale when known,
        else GTK's integer scale."""
        s = self._native_cap and (1.0 / self._native_cap)
        return float(s or self.get_scale_factor() or 1)

    # ---------------------------------------------------------------- surfaces
    def _rebuild_surfaces(self):
        self._update_subtitle()
        self.base_surface, self._base_buf = pil_to_surface(self.base_image)
        blur = make_pixelated(self.base_image, self.blur_block)
        self.blur_surface, self._blur_buf = pil_to_surface(blur)

    # ---------------------------------------------------------------------- UI
    def _build_ui(self):
        self.get_style_context().add_class("cs-window")
        install_theme(self._dark)
        sc = self.get_scale_factor()

        # ---- header: history on the left, the three exits on the right ----
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "CosmicShot"
        self.header = header
        self._update_subtitle()
        self.set_titlebar(header)

        hist = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.undo_b = self._icon_button("undo", "Undo (Ctrl+Z)", lambda *_: self.undo())
        self.redo_b = self._icon_button("redo", "Redo (Ctrl+Shift+Z)", lambda *_: self.redo())
        hist.pack_start(self.undo_b, False, False, 0)
        hist.pack_start(self.redo_b, False, False, 0)
        header.pack_start(hist)

        copy_b = Gtk.Button(label="Copy")
        copy_b.get_style_context().add_class("cs-primary")
        copy_b.set_tooltip_text("Copy the image to the clipboard and close\n"
                                "(Ctrl+C copies the selected element instead)")
        copy_b.connect("clicked", lambda *_: self.do_copy())
        save_b = Gtk.Button(label="Save")
        save_b.get_style_context().add_class("cs-secondary")
        save_b.set_tooltip_text("Save PNG and close (Ctrl+S)")
        save_b.connect("clicked", lambda *_: self.do_save())
        self.upload_b = Gtk.Button(label="Upload")
        self.upload_b.get_style_context().add_class("cs-secondary")
        self.upload_b.set_tooltip_text("Upload and copy a shareable link (Ctrl+U)")
        self.upload_b.connect("clicked", lambda *_: self.do_upload())
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if self.cfg.get("upload_enabled", True):
            actions.pack_start(self.upload_b, False, False, 0)
        actions.pack_start(save_b, False, False, 0)
        actions.pack_start(copy_b, False, False, 0)
        header.pack_end(actions)
        for b in (copy_b, save_b, self.upload_b):
            self._hand_on_hover(b)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.get_style_context().add_class("cs-root")
        self.add(root)

        # ---- toolbar: centred content inside a horizontal scroller, so the
        #      window is never forced wider than a small screen ----
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("cs-toolbar")
        root.pack_start(bar, False, False, 0)
        self.toolbar = bar
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_width(True)
        scroller.set_propagate_natural_height(True)
        scroller.set_overlay_scrolling(True)
        bar.pack_start(scroller, True, True, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.set_halign(Gtk.Align.CENTER)
        scroller.add(content)
        self.toolbar_content = content
        toolbar = content  # contextual controls below pack into this row

        # tools: one segmented group, sub-grouped by kind with hairline dividers
        toolgroup = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        toolgroup.get_style_context().add_class("cs-group")
        toolbar.pack_start(toolgroup, False, False, 0)
        group = None
        self.tool_buttons = {}
        self._tool_images = {}
        for key, label, _glyph in TOOLS:
            if key in self._TOOL_BREAKS:
                d = Gtk.Box()
                d.get_style_context().add_class("cs-divider")
                toolgroup.pack_start(d, False, False, 0)
            btn = Gtk.RadioButton.new_from_widget(group)
            btn.set_mode(False)  # render as toggle button, not radio dot
            img = Gtk.Image.new_from_surface(
                icons.surface(key, 18, self._pal["icon"], sc))
            btn.set_image(img)
            btn.set_always_show_image(True)
            self._tool_images[key] = img
            btn.get_style_context().add_class("cs-tool")
            btn.set_tooltip_text(label)
            btn.connect("toggled", self.on_tool_toggled, key)
            group = group or btn
            toolgroup.pack_start(btn, False, False, 0)
            self.tool_buttons[key] = btn
            self._hand_on_hover(btn)

        # colour: swatches + custom, in their own group
        swgroup = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        swgroup.get_style_context().add_class("cs-group")
        toolbar.pack_start(swgroup, False, False, 0)
        self._swatches = []
        for hexc in self.cfg["palette"]:
            sw = Gtk.Button()
            sw.set_size_request(20, 20)
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_tooltip_text(hexc)
            sw.get_style_context().add_class("cs-swatch")
            self._style_swatch(sw, hexc)
            sw.connect("clicked", self.on_color, hexc)
            swgroup.pack_start(sw, False, False, 0)
            self._swatches.append((sw, hexc.lower()))
            self._hand_on_hover(sw)
        self.color_btn = _ColorWell(self, self.color)
        self.color_btn.set_tooltip_text("Custom colour…")
        self.color_btn.set_valign(Gtk.Align.CENTER)
        self.color_btn.get_style_context().add_class("cs-colorwell")
        self.color_btn.connect("color-set", self.on_custom_color)
        swgroup.pack_start(self.color_btn, False, False, 2)
        self._hand_on_hover(self.color_btn)
        self._update_swatch_selection(self.color)

        # ---- contextual controls: only the ones for the active tool or
        #      selection are visible (no_show_all keeps show_all() from
        #      revealing them all at once, which used to size the window to
        #      the sum of every control) ----
        ctx = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.pack_start(ctx, False, False, 0)

        # stroke thickness (new shapes AND the selected shape)
        self.width_scale, self.thick_ctl = self._slider_control(
            "stroke", "Stroke thickness", self.width, 1, 40, 1, self._on_width_changed,
            curve=2.0)
        ctx.pack_start(self.thick_ctl, False, False, 0)

        # lock toggles, shown as chips
        self.snap_check = self._chip(f"{self.SNAP_DEG}° snap", self.snap_angles,
                                     f"Lock lines and arrows to {self.SNAP_DEG}° steps.\n"
                                     "Off: hold Shift to snap.  On: hold Shift for a free angle.",
                                     self._on_snap_toggled)
        self.snap_ctl = self.snap_check
        self.straight_check = self._chip("Straight", self.highlight_straight,
                                         "Lay the highlighter down as one straight stroke, snapped to "
                                         f"{self.SNAP_DEG}° steps — so it follows a line of text.\n"
                                         "Off: the stroke follows your hand.  Hold Shift to invert.",
                                         self._on_straight_toggled)
        self.hl_ctl = self.straight_check
        self.circle_check = self._chip("Circle", self.circle_lock,
                                       "Force a perfect circle instead of a free ellipse.\n"
                                       "Off: free ellipse.  Hold Shift to invert.",
                                       self._on_circle_toggled)
        self.circle_ctl = self.circle_check
        for c in (self.snap_ctl, self.hl_ctl, self.circle_ctl):
            ctx.pack_start(c, False, False, 0)
        # a divider between the slider and the lock options, visible only
        # when one of the lock options is
        self._lock_div = Gtk.Box()
        self._lock_div.get_style_context().add_class("cs-divider")
        ctx.pack_start(self._lock_div, False, False, 0)
        ctx.reorder_child(self._lock_div, 1)

        # crop aspect ratio: a menu button listing the ratios
        cur = self.cfg.get("crop_ratio", "Free")
        if cur not in dict(CROP_RATIOS):
            cur = "Free"
        self.crop_ctl, self._ratio_label = self._menu_button(
            cur, "Hold the crop to a strict aspect ratio.\n"
                 "Shift frees a set ratio, or squares a free crop.")
        pop, grid = self._popover_grid(self.crop_ctl, "Aspect ratio")
        self._ratio_buttons = {}
        rgrp = None
        for i, (label, _r) in enumerate(CROP_RATIOS):
            b = Gtk.RadioButton.new_with_label_from_widget(rgrp, label)
            b.set_mode(False)
            b.get_style_context().add_class("cs-row")
            b.set_active(label == cur)
            b.connect("toggled", self._on_ratio_toggled, label, pop)
            rgrp = rgrp or b
            grid.attach(b, i % 2, 1 + i // 2, 1, 1)
            self._ratio_buttons[label] = b
        grid.show_all()
        ctx.pack_start(self.crop_ctl, False, False, 0)

        # text: family (menu listing each font in its own face), size, alignment
        self.font_ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._fonts = _available_fonts()
        if self.text_font not in self._fonts:
            self.text_font = self._fonts[0]
        self.font_btn, self._font_label = self._menu_button(self.text_font, "Text font")
        fpop, fgrid = self._popover_grid(self.font_btn, "Font")
        fgrp = None
        self._font_buttons = {}
        for i, fam in enumerate(self._fonts):
            b = Gtk.RadioButton.new_from_widget(fgrp)
            b.set_mode(False)
            lab = Gtk.Label(xalign=0)
            lab.set_markup(f'<span font_family="{GLib.markup_escape_text(fam)}">'
                           f'{GLib.markup_escape_text(fam)}</span>')
            b.add(lab)
            b.get_style_context().add_class("cs-row")
            b.set_active(fam == self.text_font)
            b.connect("toggled", self._on_font_toggled, fam, fpop)
            fgrp = fgrp or b
            fgrid.attach(b, 0, 1 + i, 2, 1)
            self._font_buttons[fam] = b
        fgrid.show_all()
        self.font_ctl.pack_start(self.font_btn, False, False, 0)
        fadj = Gtk.Adjustment(value=self.font_size, lower=8, upper=160, step_increment=2)
        self.font_spin = Gtk.SpinButton(adjustment=fadj, climb_rate=1, digits=0)
        self.font_spin.set_tooltip_text("Text size")
        self.font_spin.set_width_chars(3)
        self.font_spin.set_valign(Gtk.Align.CENTER)
        self.font_spin.connect("value-changed", self._on_font_changed)
        self.font_ctl.pack_start(self.font_spin, False, False, 0)
        agroup = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        agroup.get_style_context().add_class("cs-group")
        self.align_buttons = {}
        self._align_images = {}
        agrp = None
        for key, tip in [("left", "Align left"), ("center", "Centre"),
                         ("right", "Align right"), ("justify", "Justify")]:
            b = Gtk.RadioButton.new_from_widget(agrp)
            b.set_mode(False)
            img = Gtk.Image.new_from_surface(
                icons.surface("align-" + key, 16, self._pal["icon"], sc))
            b.set_image(img)
            b.set_always_show_image(True)
            b.get_style_context().add_class("cs-tool")
            b.set_tooltip_text(tip)
            b.connect("toggled", self._on_align, key)
            agrp = agrp or b
            agroup.pack_start(b, False, False, 0)
            self.align_buttons[key] = b
            self._align_images[key] = img
            self._hand_on_hover(b)
        self.font_ctl.pack_start(agroup, False, False, 0)
        ctx.pack_start(self.font_ctl, False, False, 0)

        # blur strength / spotlight darkness: direct sliders (1:1 feedback)
        self.blur_spin, self.blur_ctl = self._slider_control(
            "drop", "Blur / pixelation strength", self.blur_block, 2, 60, 1,
            self._on_blur_changed, curve=1.6)
        ctx.pack_start(self.blur_ctl, False, False, 0)
        self.dark_spin, self.dark_ctl = self._slider_control(
            "moon", "How dark the area outside the spotlight is (%)",
            self.spotlight_darkness * 100, 0, 95, 5, self._on_dark_changed)
        ctx.pack_start(self.dark_ctl, False, False, 0)

        for w in (self.thick_ctl, self.snap_ctl, self.hl_ctl, self.circle_ctl,
                  self.crop_ctl, self.font_ctl, self.blur_ctl, self.dark_ctl,
                  self._lock_div):
            w.show_all()            # children visible (show_all is a no-op
            w.set_no_show_all(True)  # once no_show_all is set), then park
            w.hide()                 # the control until its tool is active

        # ---- canvas, with floating capsules for zoom and crop ----
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_can_focus(True)  # so it can take key input for text editing
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.canvas.connect("draw", self.on_canvas_draw)
        self.canvas.connect("button-press-event", self.on_canvas_press)
        self.canvas.connect("button-release-event", self.on_canvas_release)
        self.canvas.connect("motion-notify-event", self.on_canvas_motion)
        self.canvas.connect("scroll-event", self.on_canvas_scroll)
        self.canvas.connect("size-allocate", lambda *_: self._update_zoom_label())
        self.canvas.connect("realize",
                             lambda *_: self._set_canvas_cursor(self._tool_cursor()))

        self.overlay = Gtk.Overlay()
        self.overlay.add(self.canvas)
        root.pack_start(self.overlay, True, True, 0)

        zoom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        zoom.get_style_context().add_class("cs-float")
        zoom.set_halign(Gtk.Align.END)
        zoom.set_valign(Gtk.Align.END)
        zoom.set_margin_end(14)
        zoom.set_margin_bottom(14)
        zoom.pack_start(self._icon_button("zoom-out", "Zoom out (−)",
                                          lambda *_: self.set_zoom(self._zoom / 1.25), 16),
                        False, False, 0)
        self.zoom_label = Gtk.Button(label="100%")
        self.zoom_label.get_style_context().add_class("cs-flat")
        self.zoom_label.set_tooltip_text("Fit to window (Ctrl+0)")
        self.zoom_label.connect("clicked", lambda *_: self.reset_zoom())
        self._hand_on_hover(self.zoom_label)
        zoom.pack_start(self.zoom_label, False, False, 0)
        zoom.pack_start(self._icon_button("zoom-in", "Zoom in (+, or Ctrl+scroll)",
                                          lambda *_: self.set_zoom(self._zoom * 1.25), 16),
                        False, False, 0)
        self.overlay.add_overlay(zoom)

        # crop apply/cancel capsule (hidden until a crop is drawn)
        self.crop_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.crop_bar.get_style_context().add_class("cs-float")
        self.crop_bar.set_halign(Gtk.Align.CENTER)
        self.crop_bar.set_valign(Gtk.Align.END)
        self.crop_bar.set_margin_bottom(14)
        cancel_b = Gtk.Button(label="Cancel")
        cancel_b.get_style_context().add_class("cs-flat")
        cancel_b.connect("clicked", lambda *_: self.cancel_crop())
        apply_b = Gtk.Button(label="Apply Crop")
        apply_b.get_style_context().add_class("cs-primary")
        apply_b.set_tooltip_text("Apply (Enter)")
        apply_b.connect("clicked", lambda *_: self.apply_crop())
        self.crop_bar.pack_start(cancel_b, False, False, 0)
        self.crop_bar.pack_start(apply_b, False, False, 0)
        self.crop_bar.set_no_show_all(True)
        cancel_b.show(); apply_b.show()
        self.overlay.add_overlay(self.crop_bar)

        self.connect("notify::scale-factor", lambda *_: self._recolor_icons())

        # everything is built now -> activate the default tool
        self.tool_buttons["arrow"].set_active(True)
        self._update_tool_controls()

    # Tools after which a hairline divider is drawn (select | shapes | ink |
    # marks | redaction | crop).
    _TOOL_BREAKS = ("arrow", "pen", "counter", "crop")

    # ------------------------------------------------------ widget builders
    def _icon_button(self, icon, tip, cb, size=18):
        b = Gtk.Button()
        img = Gtk.Image.new_from_surface(
            icons.surface(icon, size, self._pal["icon"], self.get_scale_factor()))
        b.set_image(img)
        b.set_always_show_image(True)
        b.get_style_context().add_class("cs-flat")
        b.get_style_context().add_class("cs-icon")
        b.set_tooltip_text(tip)
        b.set_valign(Gtk.Align.CENTER)
        b.connect("clicked", cb)
        self._chrome_icons = getattr(self, "_chrome_icons", [])
        self._chrome_icons.append((img, icon, size))
        self._hand_on_hover(b)
        return b

    def _chip(self, label, active, tip, cb):
        """An on/off option shown as a labelled checkbox."""
        b = Gtk.CheckButton(label=label)
        b.set_active(active)
        b.get_style_context().add_class("cs-check")
        b.set_tooltip_text(tip)
        b.set_valign(Gtk.Align.CENTER)
        b.connect("toggled", cb)
        self._hand_on_hover(b)
        return b

    def _slider_control(self, icon, tip, value, lo, hi, step, cb, curve=1.0):
        """[glyph] [slider] [value] — returns (scale, container).

        With curve > 1 the slider position maps to the value on a power
        curve, so small values (where a 1px difference matters) get most of
        the travel and large ones stay reachable.  cb receives the value.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.get_style_context().add_class("cs-slider")
        box.set_tooltip_text(tip)
        img = Gtk.Image.new_from_surface(
            icons.surface(icon, 16, self._pal["icon"], self.get_scale_factor()))
        self._chrome_icons = getattr(self, "_chrome_icons", [])
        self._chrome_icons.append((img, icon, 16))
        box.pack_start(img, False, False, 4)
        span = float(hi - lo)

        def to_value(pos):
            v = lo + span * (pos / 1000.0) ** curve
            return max(lo, min(hi, round(v / step) * step))

        def to_pos(v):
            t = max(0.0, min(1.0, (v - lo) / span))
            return 1000.0 * t ** (1.0 / curve)

        adj = Gtk.Adjustment(value=to_pos(value), lower=0, upper=1000,
                             step_increment=1, page_increment=50)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        scale.set_draw_value(False)
        scale.set_size_request(150, 28)   # tall hit area; track drawn centred
        scale.set_valign(Gtk.Align.CENTER)
        val = Gtk.Label(label=str(int(round(value))))
        val.get_style_context().add_class("cs-value")
        val.set_width_chars(2)
        val.set_xalign(0.0)

        last = [to_value(to_pos(value))]

        def changed(sc):
            v = to_value(sc.get_value())
            if v == last[0]:
                return
            last[0] = v
            val.set_text(str(int(round(v))))
            cb(v)

        def nudge(direction):
            # arrow keys / wheel move one real step, not one slider pixel
            v = max(lo, min(hi, last[0] + direction * step))
            scale.set_value(to_pos(v))

        def on_scroll(_w, ev):
            ok, dx, dy = ev.get_scroll_deltas()
            if ev.direction == Gdk.ScrollDirection.UP or (ok and dy < 0):
                nudge(+1)
            elif ev.direction == Gdk.ScrollDirection.DOWN or (ok and dy > 0):
                nudge(-1)
            return True

        def on_key(_w, ev):
            if ev.keyval in (Gdk.KEY_Right, Gdk.KEY_Up):
                nudge(+1); return True
            if ev.keyval in (Gdk.KEY_Left, Gdk.KEY_Down):
                nudge(-1); return True
            return False

        scale.connect("value-changed", changed)
        box.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        scale.connect("scroll-event", on_scroll)
        scale.connect("key-press-event", on_key)
        box.pack_start(scale, False, False, 0)
        box.pack_start(val, False, False, 6)
        self._hand_on_hover(scale)
        return scale, box

    def _menu_button(self, text, tip):
        """A compact 'Label ⌄' button that opens a popover."""
        mb = Gtk.MenuButton()
        mb.get_style_context().add_class("cs-chip")
        mb.set_tooltip_text(tip)
        mb.set_valign(Gtk.Align.CENTER)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        lab = Gtk.Label(label=text)
        lab.set_max_width_chars(14)
        lab.set_ellipsize(3)  # Pango.EllipsizeMode.END
        inner.pack_start(lab, False, False, 0)
        chev = Gtk.Image.new_from_surface(
            icons.surface("chevron", 12, self._pal["icon"], self.get_scale_factor()))
        self._chrome_icons = getattr(self, "_chrome_icons", [])
        self._chrome_icons.append((chev, "chevron", 12))
        inner.pack_start(chev, False, False, 0)
        mb.add(inner)
        self._hand_on_hover(mb)
        return mb, lab

    def _popover_grid(self, menubutton, caption):
        pop = Gtk.Popover()
        pop.get_style_context().add_class("cs-popover")
        grid = Gtk.Grid(column_spacing=4, row_spacing=2)
        grid.get_style_context().add_class("cs-pop")
        cap = Gtk.Label(label=caption.upper(), xalign=0)
        cap.get_style_context().add_class("cs-caption")
        cap.set_margin_bottom(4)
        cap.set_margin_start(4)
        grid.attach(cap, 0, 0, 2, 1)
        grid.show_all()
        pop.add(grid)
        menubutton.set_popover(pop)
        return pop, grid

    def _on_ratio_toggled(self, btn, label, pop):
        if not btn.get_active():
            return
        self._ratio_label.set_text(label)
        self._set_ratio(label)
        pop.popdown()

    def _on_font_toggled(self, btn, fam, pop):
        if not btn.get_active():
            return
        self._font_label.set_text(fam)
        self._on_font_family(fam)
        pop.popdown()

    def _update_subtitle(self):
        hdr = getattr(self, "header", None)
        if hdr is not None:
            hdr.props.subtitle = f"{self.base_image.width} × {self.base_image.height}"

    def _update_zoom_label(self):
        lab = getattr(self, "zoom_label", None)
        if lab is None or self.canvas.get_allocated_width() <= 1:
            return
        scale, _, _ = self._layout()
        lab.set_label(f"{round(scale * self._device_scale() * 100)}%")

    def _recolor_icons(self):
        """Re-render every custom glyph for the current theme and monitor
        scale; the selected tool and alignment get white glyphs on accent."""
        sc = self.get_scale_factor()
        white = (1, 1, 1, 1)
        for key, img in getattr(self, "_tool_images", {}).items():
            on = self.tool_buttons[key].get_active()
            img.set_from_surface(icons.surface(key, 18, white if on else self._pal["icon"], sc))
        for key, img in getattr(self, "_align_images", {}).items():
            on = self.align_buttons[key].get_active()
            img.set_from_surface(icons.surface("align-" + key, 16,
                                               white if on else self._pal["icon"], sc))
        for img, name, size in getattr(self, "_chrome_icons", []):
            img.set_from_surface(icons.surface(name, size, self._pal["icon"], sc))

    def _coalesced_undo(self, key):
        """Push one undo step per continuous slider gesture, not one per tick:
        a drag that changes the same property of the same shape within a short
        window is a single edit."""
        import time
        now = time.monotonic()
        last = getattr(self, "_coalesce", None)
        if last and last[0] == key and now - last[1] < 0.8:
            self._coalesce = (key, now)
            return
        self._coalesce = (key, now)
        self._push_undo()

    def _on_width_changed(self, value):
        self.width = value
        sel = self.selected
        if sel is not None and hasattr(sel, "width"):
            self._coalesced_undo(("width", id(sel)))
            sel.width = self.width
            self.canvas.queue_draw()

    # ------------------------------------------------------- angle snapping
    SNAP_DEG = 15            # predefined angle step for lines / arrows

    def _on_snap_toggled(self, btn):
        self.snap_angles = btn.get_active()
        self.cfg["angle_snap_lock"] = self.snap_angles
        config.save(self.cfg)

    def _on_straight_toggled(self, btn):
        self.highlight_straight = btn.get_active()
        self.cfg["highlight_straight"] = self.highlight_straight
        config.save(self.cfg)

    def _on_circle_toggled(self, btn):
        self.circle_lock = btn.get_active()
        self.cfg["ellipse_circle_lock"] = self.circle_lock
        config.save(self.cfg)
        # Ticking it with a circle-able shape selected reshapes it right away.
        if isinstance(self.selected, tools.Ellipse) and self.circle_lock:
            self._push_undo()
            self.selected.set_bbox(*self._square_bbox(self.selected.bbox(), "se"))
            self.canvas.queue_draw()

    def _set_ratio(self, label):
        label = label or "Free"
        self.crop_ratio = dict(CROP_RATIOS).get(label)
        self.cfg["crop_ratio"] = label
        config.save(self.cfg)
        # Re-fit a crop already on screen so the choice is visible immediately.
        if self.crop_rect:
            x, y, w, h = self.crop_rect
            self.crop_rect = self._crop_from_drag(x, y, x + w, y + h, 0)
            self.canvas.queue_draw()

    @staticmethod
    def _lock_active(locked, state):
        """A toolbar lock checkbox sets the default behaviour; Shift always
        inverts it, so either mode is one modifier away."""
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        return (not shift) if locked else shift

    def _want_snap(self, state):
        """Should this line/arrow drag snap to a fixed angle?"""
        return self._lock_active(self.snap_angles, state)

    def _want_straight(self, state):
        """Should this highlighter stroke be a single straight segment?"""
        return self._lock_active(self.highlight_straight, state)

    def _want_circle(self, state):
        """Should this ellipse be a perfect circle?"""
        return self._lock_active(self.circle_lock, state)

    def _square_bbox(self, bbox, handle):
        """Make `bbox` square, holding the edge opposite the dragged handle so
        the corner under the cursor is the one that moves."""
        x, y, w, h = bbox
        s = max(w, h)
        if "w" in handle:          # dragging the left edge -> right edge stays put
            x = x + w - s
        if "n" in handle:          # dragging the top -> bottom stays put
            y = y + h - s
        return (x, y, s, s)

    def _drag_bbox(self, x0, y0, ix, iy, square=False):
        """The rect swept from the press point (x0, y0) to (ix, iy). When
        `square`, the larger drag axis sets both sides, anchored at the press
        point so the shape still grows the way the cursor is moving."""
        w, h = abs(ix - x0), abs(iy - y0)
        if square:
            w = h = max(w, h)
        x = x0 if ix >= x0 else x0 - w
        y = y0 if iy >= y0 else y0 - h
        return (x, y, w, h)

    # ------------------------------------------------------------ crop ratio
    def _crop_ratio_active(self, state):
        """The ratio this crop drag should hold, or None for free. Shift frees a
        set ratio and squares a free one (the usual crop convention)."""
        if state & Gdk.ModifierType.SHIFT_MASK:
            return None if self.crop_ratio else 1.0
        return self.crop_ratio

    def _crop_from_drag(self, x0, y0, ix, iy, state):
        """Crop rect for a drag from (x0, y0) to (ix, iy), honouring the chosen
        ratio and never straying outside the image — so what's outlined is
        exactly what Apply will cut (a ratio survives the edges intact)."""
        W, H = self.base_image.width, self.base_image.height
        x0 = max(0.0, min(float(W), x0))
        y0 = max(0.0, min(float(H), y0))
        right, down = ix >= x0, iy >= y0
        room_w = (W - x0) if right else x0      # space available from the anchor
        room_h = (H - y0) if down else y0
        w, h = abs(ix - x0), abs(iy - y0)
        ratio = self._crop_ratio_active(state)
        if ratio:
            if w >= h * ratio:                  # grow to cover the drag...
                h = w / ratio
            else:
                w = h * ratio
            k = min(1.0,                        # ...then shrink to fit, ratio intact
                    room_w / w if w else 1.0,
                    room_h / h if h else 1.0)
            w, h = w * k, h * k
        else:
            w, h = min(w, room_w), min(h, room_h)
        x = x0 if right else x0 - w
        y = y0 if down else y0 - h
        return (x, y, w, h)

    def _snap_point(self, x0, y0, x, y):
        """Pull (x, y) onto the nearest SNAP_DEG ray from (x0, y0), keeping the
        distance — so 0/15/…/90° lines and arrows come out exact."""
        dx, dy = x - x0, y - y0
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return x, y
        step = math.radians(self.SNAP_DEG)
        ang = round(math.atan2(dy, dx) / step) * step
        return x0 + math.cos(ang) * dist, y0 + math.sin(ang) * dist

    def _update_tool_controls(self):
        """Show the style control relevant to the active tool / selection
        (thickness / snapping / font+align / blur / darkness)."""
        # Called while the toolbar is still being built (the first tool radio
        # button emits "toggled" on creation), so bail out until it's all there.
        if any(getattr(self, n, None) is None
               for n in ("thick_ctl", "snap_ctl", "hl_ctl", "circle_ctl",
                         "crop_ctl", "font_ctl", "blur_ctl", "dark_ctl",
                         "_lock_div")):
            return
        t = self.tool
        text_ctx = (t == "text" or self.editing_text is not None
                    or isinstance(self.selected, tools.Text))
        spot_ctx = (t == "spotlight" or isinstance(self.selected, tools.Spotlight))
        # Arrow covers Line (it subclasses it), so both share the snap control.
        line_ctx = (t in ("arrow", "line") or isinstance(self.selected, tools.Arrow))
        self.font_ctl.set_visible(text_ctx)
        self.blur_ctl.set_visible(t == "blur")
        self.dark_ctl.set_visible(spot_ctx)
        self.snap_ctl.set_visible(line_ctx and not text_ctx)
        self.hl_ctl.set_visible(t == "highlight")
        self.circle_ctl.set_visible(t == "ellipse"
                                    or isinstance(self.selected, tools.Ellipse))
        self.crop_ctl.set_visible(t == "crop")
        self.thick_ctl.set_visible(
            not text_ctx and not spot_ctx and t not in ("blur", "crop"))
        self._lock_div.set_visible(any(
            w.get_visible() for w in (self.snap_ctl, self.hl_ctl, self.circle_ctl)))

    def _on_font_changed(self, spin):
        self.font_size = spin.get_value()
        sel = self.selected
        if isinstance(sel, tools.Text):
            if sel is self.editing_text:
                self._ensure_edit_undo()
            else:
                self._coalesced_undo(("size", id(sel)))
            sel.size = self.font_size
            self.canvas.queue_draw()

    def _reapply_theme(self):
        """React to a live light/dark switch: re-theme chrome, recolour icons."""
        dark = theme_mod.is_dark()
        if dark == self._dark:
            return
        self._dark = dark
        self._pal = theme_mod.palette(dark)
        self._canvas_bg = self._pal["canvas"]
        install_theme(dark)
        self._recolor_icons()
        self.canvas.queue_draw()

    def _on_font_family(self, fam):
        if not fam:
            return
        self.text_font = fam
        tgt = self.editing_text
        if tgt is None and isinstance(self.selected, tools.Text):
            tgt = self.selected
        if isinstance(tgt, tools.Text):
            if tgt is self.editing_text:
                self._ensure_edit_undo()
            else:
                self._push_undo()
            tgt.font = fam
            self.canvas.queue_draw()

    def _on_blur_changed(self, value):
        # Re-pixelating the whole image is the expensive part; do it once the
        # slider pauses (~50 ms) rather than on every tick of a drag.
        self.blur_block = int(value)
        if getattr(self, "_blur_src", None):
            GLib.source_remove(self._blur_src)
        self._blur_src = GLib.timeout_add(50, self._apply_blur_change)

    def _apply_blur_change(self):
        self._blur_src = None
        self._rebuild_surfaces()
        self.canvas.queue_draw()
        return False

    def _on_dark_changed(self, value):
        self.spotlight_darkness = value / 100.0
        # All spotlights share one combined dim layer, so the darkness applies
        # to every focus zone — not just the selected one (which, via the
        # max-darkness blend, made the slider look dead with 2+ zones).
        spots = [a for a in self.annotations if isinstance(a, tools.Spotlight)]
        if spots:
            self._coalesced_undo(("darkness",))
            for s in spots:
                s.darkness = self.spotlight_darkness
            self.canvas.queue_draw()

    def _style_swatch(self, btn, hexc):
        # only the fill colour here; shape/size/ring come from the .cs-swatch class
        css = f"button {{ background-image:none; background-color:{hexc}; }}".encode()
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        btn.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _update_swatch_selection(self, hexc):
        hexc = (hexc or "").lower()
        for sw, h in getattr(self, "_swatches", []):
            ctx = sw.get_style_context()
            if h == hexc:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    # ----------------------------------------------------------- coord mapping
    def _fit_scale(self):
        a = self.canvas.get_allocation()
        iw, ih = self.base_image.width, self.base_image.height
        fit = min(a.width / iw, a.height / ih) if iw and ih else 1
        # Plasma: like Spectacle, never upscale — one image pixel per screen
        # pixel at most (1/scale widget px at a fractional scale), shrink only
        # when the capture is larger than the window.
        if self._native_cap:
            fit = min(fit, self._native_cap)
        return fit

    def _layout(self):
        a = self.canvas.get_allocation()
        iw, ih = self.base_image.width, self.base_image.height
        scale = self._fit_scale() * self._zoom
        ox = (a.width - iw * scale) / 2 + self._panx
        oy = (a.height - ih * scale) / 2 + self._pany
        return scale, ox, oy

    def _clamp_pan(self):
        """Keep the image from being panned entirely off-screen."""
        a = self.canvas.get_allocation()
        iw, ih = self.base_image.width, self.base_image.height
        scale = self._fit_scale() * self._zoom
        sw, sh = iw * scale, ih * scale
        margin = 40
        # When the scaled image is larger than the view, allow panning across it;
        # otherwise keep it centered (pan 0).
        if sw > a.width:
            lim = (sw - a.width) / 2 + margin
            self._panx = max(-lim, min(self._panx, lim))
        else:
            self._panx = 0.0
        if sh > a.height:
            lim = (sh - a.height) / 2 + margin
            self._pany = max(-lim, min(self._pany, lim))
        else:
            self._pany = 0.0

    def set_zoom(self, zoom, cx=None, cy=None):
        """Zoom to `zoom` (clamped), keeping the point under (cx,cy) fixed."""
        zoom = max(1.0, min(zoom, 8.0))
        if cx is None:
            a = self.canvas.get_allocation()
            cx, cy = a.width / 2, a.height / 2
        ix, iy = self.to_image(cx, cy)   # image point under the cursor (old layout)
        self._zoom = zoom
        scale, _, _ = self._layout()
        a = self.canvas.get_allocation()
        iw, ih = self.base_image.width, self.base_image.height
        # Re-center so (ix,iy) stays under (cx,cy): solve for pan.
        self._panx = cx - (a.width - iw * scale) / 2 - ix * scale
        self._pany = cy - (a.height - ih * scale) / 2 - iy * scale
        self._clamp_pan()
        self._update_zoom_label()
        self.canvas.queue_draw()

    def reset_zoom(self):
        self._zoom = 1.0
        self._panx = self._pany = 0.0
        self._update_zoom_label()
        self.canvas.queue_draw()

    def on_canvas_scroll(self, _w, ev):
        # Ctrl+wheel zooms toward the cursor; plain wheel pans (great for tall
        # scrolling screenshots); Shift+wheel pans horizontally.
        dx, dy = 0.0, 0.0
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            _, sdx, sdy = ev.get_scroll_deltas()
            dx, dy = sdx, sdy
        elif ev.direction == Gdk.ScrollDirection.UP:
            dy = -1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            dy = 1
        elif ev.direction == Gdk.ScrollDirection.LEFT:
            dx = -1
        elif ev.direction == Gdk.ScrollDirection.RIGHT:
            dx = 1
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            factor = 1.0 + (-dy) * 0.12
            self.set_zoom(self._zoom * factor, ev.x, ev.y)
        else:
            step = 90
            if ev.state & Gdk.ModifierType.SHIFT_MASK:
                self._panx -= dy * step + dx * step
            else:
                self._pany -= dy * step
                self._panx -= dx * step
            self._clamp_pan()
            self.canvas.queue_draw()
        return True

    def to_image(self, wx, wy):
        scale, ox, oy = self._layout()
        return (wx - ox) / scale, (wy - oy) / scale

    def to_widget(self, ix, iy):
        scale, ox, oy = self._layout()
        return ix * scale + ox, iy * scale + oy

    # --------------------------------------------------------------- tool sel
    def on_tool_toggled(self, btn, key):
        if btn.get_active():
            self.tool = key
            self.commit_text()  # finish any pending text
            # selection persists across tools so any tool can manipulate shapes
            self._update_tool_controls()
            self._set_canvas_cursor(self._tool_cursor())
            if getattr(self, "canvas", None) is not None:
                self._recolor_icons()
                self.canvas.queue_draw()

    def _tool_cursor(self):
        """The canvas cursor for the active tool when not hovering a shape.
        The Select tool shows an open hand (grab) over the image."""
        return {"crop": "crosshair", "select": "grab"}.get(self.tool, "crosshair")

    def _set_canvas_cursor(self, name):
        """Set the cursor on the CANVAS only — never the toolbar / window chrome."""
        canvas = getattr(self, "canvas", None)
        win = canvas.get_window() if canvas is not None else None
        if win:
            try:
                win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), name))
            except TypeError:
                pass

    def _hand_on_hover(self, widget):
        """Show a pointing-hand cursor when hovering a clickable toolbar widget."""
        def apply(w):
            win = w.get_window()
            if win:
                try:
                    win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "pointer"))
                except TypeError:
                    pass
        widget.connect("realize", lambda w: apply(w))
        if widget.get_realized():
            apply(widget)

    def on_color(self, _b, hexc):
        self.color = hexc
        rgba = Gdk.RGBA(); rgba.parse(hexc)
        self.color_btn.set_rgba(rgba)
        self._update_swatch_selection(hexc)
        self._apply_color_to_selected(hexc)

    def on_custom_color(self, btn):
        rgba = btn.get_rgba()
        self.color = "#%02x%02x%02x" % (int(rgba.red * 255), int(rgba.green * 255),
                                        int(rgba.blue * 255))
        self._update_swatch_selection(self.color)  # clears the preset rings
        self._apply_color_to_selected(self.color)

    def _apply_color_to_selected(self, hexc):
        if self.selected is not None and hasattr(self.selected, "color"):
            if self.selected is self.editing_text:
                self._ensure_edit_undo()
            else:
                self._push_undo()
            self.selected.color = hexc
            self.canvas.queue_draw()

    # ------------------------------------------------------------ undo / redo
    def _snapshot(self):
        return (self.base_image, copy.deepcopy(self.annotations), self.counter_value)

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        self.redo_stack.clear()
        self.dirty = True

    def _restore(self, snap):
        base, anns, counter = snap
        rebuild = base is not self.base_image
        self.base_image = base
        self.annotations = anns
        self.counter_value = counter
        if rebuild:
            self._rebuild_surfaces()
        self.canvas.queue_draw()

    def undo(self):
        self.commit_text()
        if not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())

    # ----------------------------------------------------------- canvas input
    def on_canvas_press(self, _w, ev):
        if ev.button != 1:
            return False
        ix, iy = self.to_image(ev.x, ev.y)
        self.press_img = (ix, iy)
        self.hover_ann = None
        t = self.tool

        # Double / triple click inside the text being edited: select a word, then
        # everything (the plain press below has already seated the caret).
        if self.editing_text is not None and ev.type in (
                Gdk.EventType._2BUTTON_PRESS, Gdk.EventType._3BUTTON_PRESS):
            self._text_drag = False
            if ev.type == Gdk.EventType._3BUTTON_PRESS:
                self._sel, self._caret = 0, len(self.editing_text.text or "")
            else:
                self._select_word_at(self.editing_text.index_at(ix, iy))
            self._restart_caret()
            self.canvas.queue_draw()
            return True

        # While editing text: grab a handle to resize it (keep editing); a press
        # inside the box moves the caret / starts a selection drag; a press
        # anywhere else finalises the text.
        if self.editing_text is not None:
            h = self._hit_handle(self.editing_text, ev.x, ev.y)
            if h:
                self.active_handle = h
                self._moving = False
                self._predrag = self._snapshot()
                self._drag_committed = False
                return True
            bx, by, bw, bh = self.editing_text.bbox()
            if bx <= ix <= bx + bw and by <= iy <= by + bh:
                self._move_caret(self.editing_text.index_at(ix, iy),
                                 bool(ev.state & Gdk.ModifierType.SHIFT_MASK))
                self._text_drag = True
                return True
            # A click outside the box just FINISHES editing — it does not also
            # place a new text box. The next click starts a fresh one.
            self.commit_text()
            self.selected = None
            self._update_tool_controls()
            self.canvas.queue_draw()
            return True

        # Crop is a whole-image region tool and never grabs shapes.
        if t == "crop":
            self.crop_rect = (ix, iy, 0, 0)
            self.crop_bar.hide()
            return True

        # --- Universal grab: any tool can manipulate existing shapes. ---
        # 1) a resize handle of the currently-selected shape
        if self.selected is not None:
            h = self._hit_handle(self.selected, ev.x, ev.y)
            if h:
                self.active_handle = h
                self._moving = False
                self._predrag = self._snapshot()
                self._drag_committed = False
                return True
        # 2) the body of any shape under the cursor -> select + (drag to) move
        ann = self._topmost_at(ix, iy)
        if ann is not None:
            was_selected = self.selected is ann
            self.selected = ann
            self.active_handle = None
            self._moving = True
            self._drag_last = (ix, iy)
            self._predrag = self._snapshot()
            self._drag_committed = False
            # First click just SELECTS a text box (frame + resize/delete). Only a
            # click on an already-selected text box (no drag) enters editing.
            self._maybe_edit = (ann if isinstance(ann, tools.Text) and was_selected
                                else None)
            self._maybe_edit_pt = (ix, iy)
            self._press_moved = False
            self._update_tool_controls()
            self.canvas.queue_draw()
            return True

        # --- Empty canvas: deselect, then the active tool draws/places. ---
        self.selected = None
        self._update_tool_controls()
        if t == "text":
            self.start_text(ev.x, ev.y, ix, iy)
        elif t == "counter":
            self._push_undo()
            c = tools.Counter(ix, iy, self.counter_value, self.color,
                              radius=max(14, self.width * 3))
            self.annotations.append(c)
            self.counter_value += 1
            self.selected = c  # auto-select so handles are ready
        elif t in ("pen", "highlight"):
            cls = tools.Pen if t == "pen" else tools.Highlight
            w = self.width if t == "pen" else max(16, self.width * 5)
            col = self.color if t == "pen" else "#ffea00"
            self.draft = cls(points=[(ix, iy)], color=col, width=w)
            self._hl_raw = [(ix, iy)]  # raw path for the highlighter straight-assist
        self.canvas.queue_draw()
        return True

    def on_canvas_motion(self, _w, ev):
        ix, iy = self.to_image(ev.x, ev.y)
        # dragging inside a text box -> extend the text selection
        if self._text_drag and self.editing_text is not None:
            if self._sel is None:
                self._sel = self._caret
            self._caret = self.editing_text.index_at(ix, iy)
            self.canvas.queue_draw()
            return True
        # a grab (move/resize) is in progress?
        if self.active_handle or self._moving:
            self._select_motion(ev.x, ev.y, ix, iy, ev.state)
            return True
        # not pressing -> just update hover cursor/highlight
        if self.press_img is None:
            self._update_hover_cursor(ev.x, ev.y)
            return True
        x0, y0 = self.press_img
        t = self.tool
        if t in ("arrow", "line"):
            ex, ey = (self._snap_point(x0, y0, ix, iy)
                      if self._want_snap(ev.state) else (ix, iy))
            cls = tools.Arrow if t == "arrow" else tools.Line
            self.draft = cls(x0, y0, ex, ey, self.color, self.width)
        elif t in ("rect", "ellipse", "blur", "spotlight"):
            x, y, w, h = self._drag_bbox(
                x0, y0, ix, iy,
                square=(t == "ellipse" and self._want_circle(ev.state)))
            if t == "rect":
                self.draft = tools.Rect(x, y, w, h, self.color, self.width)
            elif t == "ellipse":
                self.draft = tools.Ellipse(x, y, w, h, self.color, self.width)
            elif t == "blur":
                self.draft = tools.Blur(x, y, w, h)
            else:
                self.draft = tools.Spotlight(x, y, w, h, self.spotlight_darkness)
        elif t == "pen" and self.draft:
            self.draft.points.append((ix, iy))
        elif t == "highlight" and self.draft:
            # Straight: one segment from the press point, angle-snapped (so a
            # highlight lands exactly along a line of text). Free: follow the hand.
            self._hl_raw.append((ix, iy))
            if self._want_straight(ev.state):
                self.draft.points = [(x0, y0), self._snap_point(x0, y0, ix, iy)]
            else:
                self.draft.points = list(self._hl_raw)
        elif t == "crop":
            self.crop_rect = self._crop_from_drag(x0, y0, ix, iy, ev.state)
        self.canvas.queue_draw()
        return True

    def on_canvas_release(self, _w, ev):
        if ev.button != 1:
            return False
        if self._text_drag:
            self._text_drag = False
            self.press_img = None
            self.canvas.queue_draw()
            return True
        # finishing a grab (move/resize)?
        if self.active_handle or self._moving:
            was_move = self._moving
            resized = self.active_handle is not None
            cand, moved = self._maybe_edit, self._press_moved
            at = self._maybe_edit_pt
            self.active_handle = None
            self._moving = False
            self._drag_last = None
            self._predrag = None
            self.press_img = None
            self._maybe_edit = None
            self._maybe_edit_pt = None
            self._press_moved = False
            self._set_canvas_cursor(self._tool_cursor())  # release closed hand
            # A resize that collapsed the shape to nothing would leave an
            # invisible but still clickable element behind — drop it instead.
            if resized and moved:
                self._drop_if_degenerate(self.selected)
            # a click (no drag) on a text box -> edit it
            if was_move and cand is not None and not moved:
                self.edit_existing(cand, at)
            return True
        t = self.tool
        if t == "crop":
            if self.crop_rect and self.crop_rect[2] > 4 and self.crop_rect[3] > 4:
                self.crop_bar.show()      # a real drag -> offer Apply / Cancel
            else:
                self.crop_rect = None     # a plain click cancels the crop
                self.crop_bar.hide()
            self.press_img = None
            self.canvas.queue_draw()
            return True
        if self.draft is not None:
            if self._draft_is_meaningful():
                self._push_undo()
                self.annotations.append(self.draft)
                self.selected = self.draft  # auto-select the new shape
            self.draft = None
        self.press_img = None
        self.canvas.queue_draw()
        return True

    # An annotation smaller than this (image px) draws nothing you can see, yet
    # still owns a click/hover zone — so it's treated as never having existed.
    MIN_EXTENT = 5

    def _is_degenerate(self, ann):
        """True when `ann` has no visible footprint: a stray click or a tiny
        jitter-drag that would otherwise leave an invisible hit zone."""
        if isinstance(ann, tools.Text):
            return not (ann.text or "").strip()
        if isinstance(ann, tools.Counter):
            return ann.radius < 2
        if isinstance(ann, tools.Pen):          # covers Highlight
            if len(ann.points) < 2:
                return True
            _x, _y, w, h = ann.bbox()
            return math.hypot(w, h) < 2
        if isinstance(ann, tools.Arrow):        # covers Line
            return math.hypot(ann.x1 - ann.x0, ann.y1 - ann.y0) < self.MIN_EXTENT
        _x, _y, w, h = ann.bbox()
        return w < self.MIN_EXTENT or h < self.MIN_EXTENT

    def _draft_is_meaningful(self):
        return self.draft is not None and not self._is_degenerate(self.draft)

    def _drop_if_degenerate(self, ann):
        """Remove `ann` if it has collapsed to nothing (never for the text box
        currently being typed into — that one is empty by definition at first)."""
        if ann is None or ann is self.editing_text:
            return False
        if ann in self.annotations and self._is_degenerate(ann):
            self.annotations.remove(ann)
            if self.selected is ann:
                self.selected = None
                self._update_tool_controls()
            self.canvas.queue_draw()
            return True
        return False

    def _prune_degenerate(self):
        """Sweep out any invisible leftovers before rendering/exporting."""
        keep = [a for a in self.annotations if not self._is_degenerate(a)]
        if len(keep) != len(self.annotations):
            if self.selected not in keep:
                self.selected = None
            self.annotations = keep

    # ----------------------------------------------------------- select tool
    def _handle_points_widget(self, ann):
        """name -> (wx, wy) handle positions in widget space. Empty for shapes
        that aren't resizable (highlights) — nothing to draw, nothing to grab."""
        if ann.handle_style == "none":
            return {}
        if ann.handle_style == "endpoints":
            pts = ann.endpoints()
        else:
            pts = _box_handle_points(ann.bbox())
        return {name: self.to_widget(px, py) for name, (px, py) in pts.items()}

    HANDLE_HALF = 7          # half-size of a drawn handle square (px)
    HANDLE_GRAB = 16         # how close (px) the cursor must be to grab a handle

    def _hit_handle(self, ann, wx, wy, tol=None):
        """Return the nearest handle within grab tolerance, or None."""
        tol = self.HANDLE_GRAB if tol is None else tol
        best, best_d = None, tol
        for name, (hx, hy) in self._handle_points_widget(ann).items():
            d = max(abs(wx - hx), abs(wy - hy))
            if d <= best_d:
                best, best_d = name, d
        return best

    def _topmost_at(self, ix, iy):
        scale, _, _ = self._layout()
        tol = 7 / scale if scale else 7
        for ann in reversed(self.annotations):
            if ann.contains(ix, iy, tol):
                return ann
        return None

    def _select_motion(self, wx, wy, ix, iy, state=0):
        if not (self.active_handle or self._moving) or self.selected is None:
            self._update_hover_cursor(wx, wy)
            return
        self._press_moved = True
        if not self._drag_committed:
            if self.selected is self.editing_text:
                self._ensure_edit_undo()   # part of the in-progress edit
            else:
                self.undo_stack.append(self._predrag)
                self.redo_stack.clear()
                self.dirty = True
            self._drag_committed = True
        sel = self.selected
        if self.active_handle:
            if sel.handle_style == "endpoints":
                # Re-aiming a line/arrow snaps to the same fixed angles as
                # drawing one, pivoting around the endpoint that stays put.
                if isinstance(sel, tools.Arrow) and self._want_snap(state):
                    pts = sel.endpoints()
                    ax, ay = pts["end" if self.active_handle == "start" else "start"]
                    ix, iy = self._snap_point(ax, ay, ix, iy)
                sel.set_endpoint(self.active_handle, ix, iy)
            else:
                box = _resize_bbox(sel.bbox(), self.active_handle, ix, iy)
                if isinstance(sel, tools.Ellipse) and self._want_circle(state):
                    box = self._square_bbox(box, self.active_handle)
                sel.set_bbox(*box)
        elif self._moving:
            if self.tool == "select":
                self._set_canvas_cursor("grabbing")  # closed hand while dragging
            lx, ly = self._drag_last
            sel.move(ix - lx, iy - ly)
            self._drag_last = (ix, iy)
        self.canvas.queue_draw()

    def _update_hover_cursor(self, wx, wy):
        """Hover feedback for ANY tool: resize/move cursor + highlight over shapes
        (applied to the canvas only)."""
        base = self._tool_cursor()
        name = base
        hover = None
        if self.selected is not None:
            h = self._hit_handle(self.selected, wx, wy)
            if h:
                name = {"nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize",
                        "se": "se-resize", "n": "n-resize", "s": "s-resize",
                        "e": "e-resize", "w": "w-resize",
                        "start": "crosshair", "end": "crosshair"}.get(h, base)
        if name == base:  # not over a handle -> check shape bodies
            ann = self._topmost_at(*self.to_image(wx, wy))
            if ann is not None:
                hover = ann
                # Over an already-selected text box -> I-beam: a click edits it.
                if ann is self.selected and isinstance(ann, tools.Text):
                    name = "text"
                else:
                    name = "move"
        if hover is not self.hover_ann:
            self.hover_ann = hover
            self.canvas.queue_draw()
        self._set_canvas_cursor(name)

    def delete_selected(self):
        if self.selected is not None and self.selected in self.annotations:
            self._push_undo()
            self.annotations.remove(self.selected)
            self.selected = None
            self._update_tool_controls()
            self.canvas.queue_draw()

    # ---------------------------------------------- element copy / cut / paste
    PASTE_OFFSET = 18        # image px each pasted copy is nudged by

    def copy_element(self):
        """Put the selected annotation on the editor's element clipboard."""
        if self.selected is None:
            return False
        self._ann_clip = copy.deepcopy(self.selected)
        self._paste_n = 0
        return True

    def cut_element(self):
        if self.copy_element():
            self.delete_selected()
            return True
        return False

    def paste_element(self):
        """Drop a copy of the clipboarded element, offset so it doesn't hide the
        original. Repeated pastes cascade instead of stacking."""
        if self._ann_clip is None:
            return False
        ann = copy.deepcopy(self._ann_clip)
        self._push_undo()
        if isinstance(ann, tools.Counter):
            ann.number = self.counter_value      # a duplicated step gets the next number
            self.counter_value += 1
        self._paste_n += 1
        off = self.PASTE_OFFSET * self._paste_n
        ann.move(off, off)
        self._keep_in_image(ann)
        self.annotations.append(ann)
        self.selected = ann
        self._update_tool_controls()
        self.canvas.queue_draw()
        return True

    def _keep_in_image(self, ann):
        """Nudge a pasted element back if the offset pushed it off the image."""
        x, y, w, h = ann.bbox()
        W, H = self.base_image.width, self.base_image.height
        dx = min(0.0, (W - 8) - x) if x > W - 8 else 0.0
        dy = min(0.0, (H - 8) - y) if y > H - 8 else 0.0
        if dx or dy:
            ann.move(dx, dy)
            self._paste_n = 0     # restart the cascade from the original spot

    # ------------------------------------------------------------------ text
    def start_text(self, wx, wy, ix, iy):
        """Begin a new Text box in place on the canvas. It behaves like any other
        shape — selectable, movable, width-resizable via its handles — while you
        type. Font size changes only via the Font size control."""
        self.commit_text()
        ann = tools.Text(ix, iy, "", self.color, self.font_size,
                         align=self.text_align, font=self.text_font)
        # Start with a roomy box so there's space to type before it auto-grows.
        ann.box_w = max(ann.min_width(), 320.0)
        self.editing_text = ann
        self.selected = ann
        self._caret = 0
        self._sel = None
        self._edit_snapshot = self._snapshot()
        self._edit_undo_pushed = False
        self._start_caret()
        self._update_tool_controls()
        self.canvas.grab_focus()
        self.canvas.queue_draw()

    def edit_existing(self, ann, at=None):
        """Re-enter editing on an already-committed Text annotation (on click).
        `at` is the image-space point clicked, so the caret lands where the user
        actually pointed instead of at the end of the text."""
        self.commit_text()
        self.editing_text = ann   # stays in self.annotations
        self.selected = ann
        self.text_align = ann.align
        self._sync_align_buttons(ann.align)
        self._caret = (ann.index_at(*at) if at else len(ann.text or ""))
        self._sel = None
        self._edit_snapshot = self._snapshot()
        self._edit_undo_pushed = False
        self._start_caret()
        self._update_tool_controls()
        self.canvas.grab_focus()
        self.canvas.queue_draw()

    def _ensure_edit_undo(self):
        """Push the pre-edit snapshot once, on the first actual change."""
        if self.editing_text is not None and not self._edit_undo_pushed:
            self.undo_stack.append(self._edit_snapshot)
            self.redo_stack.clear()
            self.dirty = True
            self._edit_undo_pushed = True

    def commit_text(self):
        """Finalise the text being edited: keep it if non-empty, else discard."""
        ann = self.editing_text
        if ann is None:
            return
        self.editing_text = None
        self._text_drag = False
        self._caret = 0
        self._sel = None
        self._stop_caret()
        is_new = ann not in self.annotations
        if ann.text.strip():
            if is_new:
                self.annotations.append(ann)  # undo already pushed on first edit
            self.selected = ann
        else:
            if not is_new:
                self.annotations.remove(ann)
            if self.selected is ann:
                self.selected = None
        self._edit_snapshot = None
        self._edit_undo_pushed = False
        self._update_tool_controls()
        self.canvas.queue_draw()

    def _on_align(self, btn, key):
        if not btn.get_active():
            return
        self._recolor_icons()
        self.text_align = key
        tgt = self.editing_text
        if tgt is None and isinstance(self.selected, tools.Text):
            tgt = self.selected
        if isinstance(tgt, tools.Text):
            if tgt is self.editing_text:
                self._ensure_edit_undo()
            else:
                self._push_undo()
            tgt.align = key
            self.canvas.queue_draw()

    def _sync_align_buttons(self, key):
        btn = self.align_buttons.get(key)
        if btn is not None and not btn.get_active():
            btn.set_active(True)

    # ------------------------------------------------- caret & selection model
    def _has_selection(self):
        return self._sel is not None and self._sel != self._caret

    def _sel_range(self):
        """The selected character range as (start, end); empty when start==end."""
        if not self._has_selection():
            return (self._caret, self._caret)
        return (min(self._sel, self._caret), max(self._sel, self._caret))

    def _move_caret(self, idx, extend=False):
        n = len(self.editing_text.text or "")
        if extend:
            if self._sel is None:
                self._sel = self._caret
        else:
            self._sel = None
        self._caret = max(0, min(n, idx))
        self._restart_caret()          # stay solid while navigating
        self.canvas.queue_draw()

    def _select_word_at(self, idx):
        t = self.editing_text.text or ""
        a = b = max(0, min(len(t), idx))
        while a > 0 and not t[a - 1].isspace():
            a -= 1
        while b < len(t) and not t[b].isspace():
            b += 1
        self._sel, self._caret = a, b

    def _insert_text(self, s):
        """Insert `s` at the caret, replacing any selection."""
        ann = self.editing_text
        self._ensure_edit_undo()
        a, b = self._sel_range()
        t = ann.text or ""
        ann.text = t[:a] + s + t[b:]
        self._caret, self._sel = a + len(s), None
        self._restart_caret()
        self.canvas.queue_draw()

    def _delete_text_selection(self):
        if not self._has_selection():
            return False
        ann = self.editing_text
        self._ensure_edit_undo()
        a, b = self._sel_range()
        t = ann.text or ""
        ann.text = t[:a] + t[b:]
        self._caret, self._sel = a, None
        self._restart_caret()
        self.canvas.queue_draw()
        return True

    def _copy_text_selection(self):
        a, b = self._sel_range()
        if a == b:
            return False
        export.copy_text_to_clipboard((self.editing_text.text or "")[a:b])
        return True

    @staticmethod
    def _word_left(text, i):
        while i > 0 and text[i - 1].isspace():
            i -= 1
        while i > 0 and not text[i - 1].isspace():
            i -= 1
        return i

    @staticmethod
    def _word_right(text, i):
        n = len(text)
        while i < n and not text[i].isspace():
            i += 1
        while i < n and text[i].isspace():
            i += 1
        return i

    def _caret_line_step(self, direction):
        """Caret index one visual line up (-1) or down (+1), holding the column.
        Uses the Pango layout, so it follows wrapping and alignment."""
        from gi.repository import Pango
        ann = self.editing_text
        n = len(ann.text or "")
        try:
            layout = ann.layout()
            pos = layout.index_to_pos(ann.index_to_byte(self._caret))
            height = pos.height or int(ann.size * Pango.SCALE)
            y = pos.y + height // 2 + direction * height
            if y < 0:
                return 0
            if y > layout.get_extents()[1].height:
                return n
            _inside, bidx, trailing = layout.xy_to_index(pos.x, y)
            return max(0, min(n, ann.byte_to_index(bidx) + (trailing or 0)))
        except Exception:
            return self._caret

    def _line_edge(self, direction):
        """Caret index at the start (-1) or end (+1) of the current visual line."""
        ann = self.editing_text
        try:
            _no, line = ann.line_of(self._caret)
            b = line.start_index if direction < 0 else line.start_index + line.length
            return ann.byte_to_index(b)
        except Exception:
            return 0 if direction < 0 else len(ann.text or "")

    def _text_key(self, ev):
        """Handle a key while editing text. Returns True (always consumed) so no
        editor shortcut can fire mid-sentence."""
        ann = self.editing_text
        k = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        text = ann.text or ""
        n = len(text)

        # --- clipboard / select all ---
        if ctrl and k in (Gdk.KEY_a, Gdk.KEY_A):
            self._sel, self._caret = 0, n
            self._restart_caret(); self.canvas.queue_draw(); return True
        if ctrl and k in (Gdk.KEY_c, Gdk.KEY_C):
            self._copy_text_selection(); return True
        if ctrl and k in (Gdk.KEY_x, Gdk.KEY_X):
            if self._copy_text_selection():
                self._delete_text_selection()
            return True
        if ctrl and k in (Gdk.KEY_v, Gdk.KEY_V):
            self._paste_into_text(); return True
        if ctrl and k in (Gdk.KEY_z, Gdk.KEY_Z):
            # Undo works on whole edits: finish this one first, then step back.
            self.commit_text()
            self.redo() if shift else self.undo()
            return True

        # --- navigation (Shift extends the selection) ---
        nav = None
        if k in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            nav = self._word_left(text, self._caret) if ctrl else self._caret - 1
        elif k in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            nav = self._word_right(text, self._caret) if ctrl else self._caret + 1
        elif k in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            nav = self._caret_line_step(-1)
        elif k in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            nav = self._caret_line_step(+1)
        elif k in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            nav = 0 if ctrl else self._line_edge(-1)
        elif k in (Gdk.KEY_End, Gdk.KEY_KP_End):
            nav = n if ctrl else self._line_edge(+1)
        if nav is not None:
            # A plain arrow with an active selection collapses it to that side.
            if not shift and self._has_selection() and not ctrl:
                a, b = self._sel_range()
                if k in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
                    nav = a
                elif k in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
                    nav = b
            self._move_caret(nav, shift)
            return True

        # --- editing ---
        if k == Gdk.KEY_Escape:
            self.commit_text(); return True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if shift:
                self._insert_text("\n")
            else:
                self.commit_text()
            return True
        if k == Gdk.KEY_BackSpace:
            if not self._delete_text_selection() and self._caret > 0:
                self._ensure_edit_undo()
                ann.text = text[:self._caret - 1] + text[self._caret:]
                self._caret -= 1
                self._restart_caret()
                self.canvas.queue_draw()
            return True
        if k in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            if not self._delete_text_selection() and self._caret < n:
                self._ensure_edit_undo()
                ann.text = text[:self._caret] + text[self._caret + 1:]
                self._restart_caret()
                self.canvas.queue_draw()
            return True
        if ctrl:
            return True      # swallow every other Ctrl combo while typing
        ch = Gdk.keyval_to_unicode(k)
        if ch >= 32:
            self._insert_text(chr(ch))
        return True

    def _clipboard_text(self):
        import subprocess
        try:
            out = subprocess.run(["wl-paste", "-n", "-t", "text/plain"],
                                 capture_output=True, timeout=3)
            text = out.stdout.decode("utf-8", "replace")
            if text:
                return text
        except Exception:
            pass
        try:
            return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ""
        except Exception:
            return ""

    def _paste_into_text(self):
        text = self._clipboard_text()
        if text:
            self._insert_text(text)

    # caret blink ---------------------------------------------------------
    def _start_caret(self):
        self._caret_on = True
        if self._caret_src is None:
            self._caret_src = GLib.timeout_add(500, self._blink_caret)

    def _stop_caret(self):
        if self._caret_src is not None:
            GLib.source_remove(self._caret_src)
            self._caret_src = None

    def _restart_caret(self):
        """Show the caret solid and restart the blink — it must be visible while
        typing or moving, not off on the wrong half of a blink."""
        self._caret_on = True
        if self._caret_src is not None:
            GLib.source_remove(self._caret_src)
            self._caret_src = GLib.timeout_add(500, self._blink_caret)

    def _blink_caret(self):
        self._caret_on = not self._caret_on
        if self.editing_text is not None:
            self.canvas.queue_draw()
        return True

    # ------------------------------------------------------------------ crop
    def apply_crop(self):
        if not self.crop_rect:
            return
        x, y, w, h = (int(round(v)) for v in self.crop_rect)
        x = max(0, x); y = max(0, y)
        w = min(self.base_image.width - x, w)
        h = min(self.base_image.height - y, h)
        if w < 1 or h < 1:
            return
        self._push_undo()
        self.base_image = self.base_image.crop((x, y, x + w, y + h))
        # shift annotations into the new origin
        for a in self.annotations:
            self._offset_annotation(a, -x, -y)
        self._rebuild_surfaces()
        self.crop_rect = None
        self.crop_bar.hide()
        # continue editing: drop back to the Select tool on the cropped image
        self.tool_buttons["select"].set_active(True)
        self.canvas.queue_draw()

    def cancel_crop(self):
        self.crop_rect = None
        self.crop_bar.hide()
        self.canvas.queue_draw()

    @staticmethod
    def _offset_annotation(a, dx, dy):
        for attr in ("x", "y", "x0", "y0", "x1", "y1"):
            if hasattr(a, attr):
                setattr(a, attr, getattr(a, attr) + (dx if attr in ("x", "x0", "x1") else dy))
        if hasattr(a, "points"):
            a.points = [(px + dx, py + dy) for px, py in a.points]

    # --------------------------------------------------------------- drawing
    def on_canvas_draw(self, _w, cr):
        a = self.canvas.get_allocation()
        # neutral bg behind the (letterboxed) image, matching light/dark
        cr.set_source_rgb(*self._canvas_bg)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        scale, ox, oy = self._layout()
        self._draw_image_shadow(cr, ox, oy, self.base_image.width * scale,
                                self.base_image.height * scale)
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        # base image
        cr.set_source_surface(self.base_surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        ctx = _DrawCtx(self.blur_surface, self.base_image.width, self.base_image.height)
        # ONE combined spotlight dim layer (so overlapping spotlights don't stack
        # darkness), drawn over the image but under the annotations. Includes the
        # live draft rect if a spotlight is being dragged.
        spots = [a for a in self.annotations if isinstance(a, tools.Spotlight)]
        draft_rect = None
        if isinstance(self.draft, tools.Spotlight):
            draft_rect = (self.draft.x, self.draft.y, self.draft.w, self.draft.h)
        tools.Spotlight.draw_combined(cr, ctx, spots, draft_rect)
        # committed annotations (spotlights handled above; skip the edited text)
        for ann in self.annotations:
            if ann is self.editing_text or isinstance(ann, tools.Spotlight):
                continue
            cr.save(); cr.new_path(); ann.draw(cr, ctx); cr.restore()
        # live draft (non-spotlight; spotlight draft is in the combined layer)
        if self.draft is not None and not isinstance(self.draft, tools.Spotlight):
            cr.save(); cr.new_path(); self.draft.draw(cr, ctx); cr.restore()
        # text being typed in place: subtle fill behind it, then the text + caret
        if self.editing_text is not None:
            bx, by, bw, bh = self.editing_text.bbox()  # already padded
            cr.save()
            cr.set_source_rgba(1, 1, 1, 0.16)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()
            cr.restore()
            self._draw_text_selection(cr)
            cr.save(); cr.new_path(); self.editing_text.draw(cr, ctx); cr.restore()
            self._draw_caret(cr)
        cr.restore()
        # crop overlay (drawn in widget space)
        if (self.tool == "crop" and self.crop_rect
                and self.crop_rect[2] >= 1 and self.crop_rect[3] >= 1):
            self._draw_crop(cr, a)
            return False
        # hover highlight (any tool, when not the selected shape)
        if self.hover_ann is not None and self.hover_ann is not self.selected:
            self._draw_hover(cr, self.hover_ann)
        # frame: SOLID + caret = typing; DASHED = moving/resizing or just selected
        if self.editing_text is not None:
            manipulating = bool(self.active_handle or self._moving)
            self._draw_box_frame(cr, self.editing_text, dashed=manipulating)
        elif self.selected is not None:
            self._draw_box_frame(cr, self.selected, dashed=True)
        return False

    def _draw_image_shadow(self, cr, x, y, w, h):
        """Lift the screenshot off the canvas: a soft, slightly offset ambient
        shadow (stacked translucent rounded rects — cheap, no blur pass) and a
        hairline edge so light-on-light and dark-on-dark captures keep a
        visible boundary."""
        strength = self._pal.get("shadow", 0.4)
        layers = 10
        for i in range(layers, 0, -1):
            spread = i * 1.8
            cr.set_source_rgba(0, 0, 0, strength * 0.045 * (1 - i / (layers + 1)) * 2)
            icons._round_rect(cr, x - spread, y - spread + 3, w + 2 * spread,
                              h + 2 * spread, spread + 2)
            cr.fill()
        cr.set_line_width(1)
        cr.set_source_rgba(*((1, 1, 1, 0.10) if self._dark else (0, 0, 0, 0.12)))
        cr.rectangle(x - 0.5, y - 0.5, w + 1, h + 1)
        cr.stroke()

    def _draw_text_selection(self, cr):
        """Tint the selected characters, under the glyphs."""
        a, b = self._sel_range()
        if a == b:
            return
        rects = self.editing_text.selection_rects(a, b, cr)
        if not rects:
            return
        cr.save()
        cr.set_source_rgba(*ACCENT, 0.35)
        for rx, ry, rw, rh in rects:
            cr.rectangle(rx, ry, max(1.5, rw), rh)
        cr.fill()
        cr.restore()

    def _draw_caret(self, cr):
        """Draw the blinking text caret at its current position (Pango-aware, so
        it follows wrapping and alignment). Hidden while dragging (move/resize)."""
        if not self._caret_on or self.active_handle or self._moving:
            return
        ann = self.editing_text
        cx, cy, ch = ann.caret_rect(self._caret, cr)
        cr.set_source_rgba(*config.hex_to_rgba(ann.color))
        cr.set_line_width(max(1.5, ann.size * 0.06))
        cr.move_to(cx, cy)
        cr.line_to(cx, cy + ch)
        cr.stroke()

    def _draw_hover(self, cr, ann):
        x, y, w, h = ann.bbox()
        wx, wy = self.to_widget(x, y)
        scale, _, _ = self._layout()
        pad = 3
        cr.set_source_rgba(*ACCENT, 0.55)
        cr.set_line_width(1.5)
        cr.rectangle(wx - pad, wy - pad, w * scale + 2 * pad, h * scale + 2 * pad)
        cr.stroke()

    def _draw_box_frame(self, cr, ann, dashed):
        """Frame + handles around a shape. dashed=True for a plain selection
        (move/resize); solid + thicker for a text box in typing mode."""
        x, y, w, h = ann.bbox()
        wx, wy = self.to_widget(x, y)
        scale, _, _ = self._layout()
        ww, wh = w * scale, h * scale
        cr.set_source_rgba(*ACCENT, 0.95)
        if dashed:
            cr.set_line_width(1.5)
            cr.set_dash([4, 3])
        else:
            cr.set_line_width(2.5)   # solid, bolder -> "editing / typing"
            cr.set_dash([])
        cr.rectangle(wx, wy, ww, wh)
        cr.stroke()
        cr.set_dash([])
        s = self.HANDLE_HALF
        for _name, (hx, hy) in self._handle_points_widget(ann).items():
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(hx - s, hy - s, 2 * s, 2 * s)
            cr.fill_preserve()
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(2)
            cr.stroke()

    def _draw_crop(self, cr, a):
        x, y, w, h = self.crop_rect
        wx, wy = self.to_widget(x, y)
        scale, _, _ = self._layout()
        ww, wh = w * scale, h * scale
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.rectangle(0, 0, a.width, a.height)
        cr.rectangle(wx, wy, ww, wh)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()
        cr.set_source_rgb(0.0, 0.48, 1.0)
        cr.set_line_width(2)
        cr.rectangle(wx, wy, ww, wh)
        cr.stroke()

    # ------------------------------------------------------------- shortcuts
    def on_key(self, _w, ev):
        # Typing into an in-place text box takes priority over all shortcuts.
        if self.editing_text is not None:
            return self._text_key(ev)
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        k = ev.keyval
        # Escape backs out of the current step only — it never leaves the editor.
        # Copy / Save / Upload (or the window close button) are the only exits,
        # so a stray Escape can't throw away a screenshot.
        if k == Gdk.KEY_Escape:
            if self.crop_rect:
                self.cancel_crop()
            elif self.selected is not None:
                self.selected = None
                self._update_tool_controls()
                self.canvas.queue_draw()
            return True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.crop_rect:
            self.apply_crop(); return True
        # Zoom: Ctrl++ / Ctrl+- / Ctrl+0 (and bare +/- for convenience).
        if k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.set_zoom(self._zoom * 1.25); return True
        if k in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self.set_zoom(self._zoom / 1.25); return True
        if k in (Gdk.KEY_0, Gdk.KEY_KP_0) and ctrl:
            self.reset_zoom(); return True
        if k in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected is not None:
            self.delete_selected(); return True
        if ctrl and k in (Gdk.KEY_z, Gdk.KEY_Z):
            self.redo() if shift else self.undo(); return True
        if ctrl and k in (Gdk.KEY_y, Gdk.KEY_Y):
            self.redo(); return True
        # Ctrl+C/X/V act on the SELECTED ELEMENT, like any other canvas editor.
        # Copying the finished image to the clipboard is the Copy button's job —
        # it also closes the window, which is too destructive for a stray Ctrl+C.
        if ctrl and k in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_element(); return True
        if ctrl and k in (Gdk.KEY_x, Gdk.KEY_X):
            self.cut_element(); return True
        if ctrl and k in (Gdk.KEY_v, Gdk.KEY_V):
            self.paste_element(); return True
        if ctrl and k in (Gdk.KEY_s, Gdk.KEY_S):
            self.do_save(); return True
        if ctrl and k in (Gdk.KEY_u, Gdk.KEY_U):
            if self.cfg.get("upload_enabled", True):
                self.do_upload()
            return True
        # single-key tool shortcuts
        keymap = {Gdk.KEY_v: "select", Gdk.KEY_a: "arrow", Gdk.KEY_r: "rect",
                  Gdk.KEY_e: "ellipse", Gdk.KEY_l: "line", Gdk.KEY_p: "pen",
                  Gdk.KEY_h: "highlight", Gdk.KEY_t: "text", Gdk.KEY_b: "blur",
                  Gdk.KEY_o: "spotlight", Gdk.KEY_n: "counter", Gdk.KEY_x: "crop"}
        if not ctrl and k in keymap:
            self.tool_buttons[keymap[k]].set_active(True)
            return True
        return False

    # ----------------------------------------------------------- close guard
    def on_delete_event(self, *_):
        # Window-manager / header close button. Veto and confirm if there's work.
        if self._closing or not self.dirty:
            return False
        self._confirm_close()
        return True

    def _confirm_close(self):
        self.commit_text()
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            text="Discard this screenshot?")
        dlg.format_secondary_text("You have unsaved edits. Save them, or discard?")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Discard", Gtk.ResponseType.REJECT)
        save_btn = dlg.add_button("Save", Gtk.ResponseType.ACCEPT)
        save_btn.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.ACCEPT)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.REJECT:
            self._closing = True
            self.destroy()
        elif resp == Gtk.ResponseType.ACCEPT:
            self.do_save()
        # CANCEL -> stay open

    # --------------------------------------------------------------- actions
    def _render(self):
        self.commit_text()
        self._prune_degenerate()
        return export.render(self.base_surface, self.blur_surface, self.annotations)

    def do_copy(self):
        surface = self._render()
        ok = export.copy_to_clipboard(surface)
        # Copy is instant and the window closes — only surface a failure. The
        # success toast is opt-in (notify_on_action).
        if not ok:
            export.notify("Copy failed")
        elif self.cfg.get("notify_on_action"):
            export.notify("Copied to clipboard")
        self._closing = True
        self.destroy()

    def _ask_save_path(self):
        """Run a Save-as dialog; return the chosen path or None if cancelled."""
        import os
        from datetime import datetime
        dlg = Gtk.FileChooserDialog(
            title="Save screenshot", transient_for=self,
            action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Save", Gtk.ResponseType.ACCEPT)
        dlg.set_default_response(Gtk.ResponseType.ACCEPT)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name(datetime.now().strftime(self.cfg["filename_pattern"]))
        start = os.path.expanduser(self.cfg.get("save_dir") or "")
        if start:
            try:
                os.makedirs(start, exist_ok=True)
                dlg.set_current_folder(start)
            except Exception:
                pass
        flt = Gtk.FileFilter()
        flt.set_name("PNG image")
        flt.add_pattern("*.png")
        dlg.add_filter(flt)
        resp = dlg.run()
        chosen = dlg.get_filename() if resp == Gtk.ResponseType.ACCEPT else None
        dlg.destroy()
        if chosen and not chosen.lower().endswith(".png"):
            chosen += ".png"
        return chosen

    def do_save(self):
        path = None
        if self.cfg.get("ask_save_location"):
            path = self._ask_save_path()
            if not path:
                return  # cancelled — keep the editor open
        surface = self._render()
        path = export.save_to_disk(surface, self.cfg, path)
        if self.cfg.get("copy_on_save"):
            export.copy_to_clipboard(surface)
        if self.cfg.get("notify_on_action"):
            export.notify("Screenshot saved", path, path)
        self._closing = True
        self.destroy()

    def do_upload(self):
        import threading
        from . import upload
        surface = self._render()
        data = export.surface_to_png_bytes(surface)
        self.upload_b.set_sensitive(False)
        self.upload_b.set_label("Uploading…")  # the button is the progress cue

        def work():
            try:
                url = upload.upload_image(data, self.cfg)
                GLib.idle_add(self._upload_done, url, None)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._upload_done, None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _upload_done(self, url, err):
        self.upload_b.set_sensitive(True)
        self.upload_b.set_label("Upload")
        if url:
            export.copy_text_to_clipboard(url)
            export.notify("Uploaded — link copied to clipboard", url)
        else:
            export.notify("Upload failed", err or "")
        return False

    def run(self):
        """Show the editor; returns the surface to pin (or None)."""
        self.pending_pin = None
        self.show_all()
        self._update_tool_controls()
        GLib.idle_add(self._fit_to_monitor)
        # Follow a live light/dark switch while open.
        try:
            from gi.repository import Gio
            self._theme_settings = Gio.Settings.new("org.gnome.desktop.interface")
            self._theme_settings.connect("changed::color-scheme",
                                         lambda *_: self._reapply_theme())
        except Exception:
            self._theme_settings = None
        Gtk.main()
        return self.pending_pin
