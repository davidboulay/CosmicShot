"""The annotation editor window -- the heart of the CleanShot-style experience."""
import copy

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402
import cairo  # noqa: E402

from . import config, export, tools
from .imaging import pil_to_surface, make_pixelated

TOOLS = [
    ("select",    "Select / move / resize (V)", "↖"),
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
        self.blur_block = int(self.cfg.get("pixelate_block", 12))
        self.spotlight_darkness = float(self.cfg.get("spotlight_darkness", 0.6))

        self.draft = None        # in-progress annotation (live preview)
        self.press_img = None     # press point in image coords
        self.crop_rect = None     # (x, y, w, h) image coords while crop tool active
        self.text_entry = None
        self.text_pos = None
        self.pending_pin = None

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

    def _apply_window_sizing(self):
        """Size the window so the whole toolbar is visible, and never let it be
        resized narrower than the tools."""
        need_w = self.toolbar.get_preferred_width()[1] + 24  # natural toolbar width
        img_w = min(1280, self.base_image.width + 40)
        img_h = min(820, self.base_image.height + 140)
        self.set_default_size(max(need_w, img_w), img_h)
        geom = Gdk.Geometry()
        geom.min_width = need_w
        geom.min_height = 360
        self.set_geometry_hints(None, geom, Gdk.WindowHints.MIN_SIZE)

    # ---------------------------------------------------------------- surfaces
    def _rebuild_surfaces(self):
        self.base_surface, self._base_buf = pil_to_surface(self.base_image)
        blur = make_pixelated(self.base_image, self.blur_block)
        self.blur_surface, self._blur_buf = pil_to_surface(blur)

    # ---------------------------------------------------------------------- UI
    def _build_ui(self):
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "CosmicShot"
        self.set_titlebar(header)

        undo_b = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.BUTTON)
        undo_b.set_tooltip_text("Undo (Ctrl+Z)")
        undo_b.connect("clicked", lambda *_: self.undo())
        redo_b = Gtk.Button.new_from_icon_name("edit-redo-symbolic", Gtk.IconSize.BUTTON)
        redo_b.set_tooltip_text("Redo (Ctrl+Shift+Z)")
        redo_b.connect("clicked", lambda *_: self.redo())
        header.pack_start(undo_b)
        header.pack_start(redo_b)

        copy_b = Gtk.Button(label="Copy")
        copy_b.get_style_context().add_class("suggested-action")
        copy_b.set_tooltip_text("Copy to clipboard (Ctrl+C)")
        copy_b.connect("clicked", lambda *_: self.do_copy())
        save_b = Gtk.Button(label="Save")
        save_b.set_tooltip_text("Save PNG (Ctrl+S)")
        save_b.connect("clicked", lambda *_: self.do_save())
        pin_b = Gtk.Button(label="Pin")
        pin_b.set_tooltip_text("Pin to screen")
        pin_b.connect("clicked", lambda *_: self.do_pin())
        self.upload_b = Gtk.Button(label="Upload")
        self.upload_b.set_tooltip_text("Upload and copy a shareable URL (Ctrl+U)")
        self.upload_b.connect("clicked", lambda *_: self.do_upload())
        header.pack_end(copy_b)
        header.pack_end(save_b)
        header.pack_end(self.upload_b)
        header.pack_end(pin_b)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # toolbar packed directly so the window is forced at least this wide
        # (tools are always fully visible, never cut off)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_border_width(6)
        root.pack_start(toolbar, False, False, 0)
        self.toolbar = toolbar

        # tool toggle buttons (radio behavior)
        group = None
        self.tool_buttons = {}
        for key, label, glyph in TOOLS:
            btn = Gtk.RadioButton.new_from_widget(group)
            btn.set_mode(False)  # render as toggle button, not radio dot
            btn.set_label(glyph)
            btn.set_tooltip_text(label)
            btn.connect("toggled", self.on_tool_toggled, key)
            group = group or btn
            toolbar.pack_start(btn, False, False, 0)
            self.tool_buttons[key] = btn
            self._hand_on_hover(btn)

        toolbar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)

        # --- contextual style controls (placed near the tools so they're always
        #     visible without scrolling; only one shows at a time) ---
        # stroke thickness (applies to new shapes AND the selected shape)
        self.thick_ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.thick_ctl.pack_start(Gtk.Label(label="Thickness"), False, False, 0)
        adj = Gtk.Adjustment(value=self.width, lower=1, upper=60, step_increment=1)
        self.width_spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self.width_spin.set_tooltip_text("Stroke thickness")
        self.width_spin.connect("value-changed", self._on_width_changed)
        self.thick_ctl.pack_start(self.width_spin, False, False, 0)
        toolbar.pack_start(self.thick_ctl, False, False, 0)

        # font size (only for the Text tool / selected text)
        self.font_ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.font_ctl.pack_start(Gtk.Label(label="Font size"), False, False, 0)
        fadj = Gtk.Adjustment(value=self.font_size, lower=8, upper=160, step_increment=2)
        self.font_spin = Gtk.SpinButton(adjustment=fadj, climb_rate=1, digits=0)
        self.font_spin.set_tooltip_text("Text font size")
        self.font_spin.connect("value-changed", self._on_font_changed)
        self.font_ctl.pack_start(self.font_spin, False, False, 0)
        toolbar.pack_start(self.font_ctl, False, False, 0)

        # blur strength (only for the Blur tool)
        self.blur_ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.blur_ctl.pack_start(Gtk.Label(label="Blur"), False, False, 0)
        badj = Gtk.Adjustment(value=self.blur_block, lower=2, upper=60, step_increment=1)
        self.blur_spin = Gtk.SpinButton(adjustment=badj, climb_rate=1, digits=0)
        self.blur_spin.set_tooltip_text("Blur / pixelation strength")
        self.blur_spin.connect("value-changed", self._on_blur_changed)
        self.blur_ctl.pack_start(self.blur_spin, False, False, 0)
        toolbar.pack_start(self.blur_ctl, False, False, 4)

        # spotlight darkness (only for the Spotlight tool)
        self.dark_ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.dark_ctl.pack_start(Gtk.Label(label="Darkness %"), False, False, 0)
        dadj = Gtk.Adjustment(value=self.spotlight_darkness * 100, lower=0, upper=95,
                              step_increment=5)
        self.dark_spin = Gtk.SpinButton(adjustment=dadj, climb_rate=1, digits=0)
        self.dark_spin.set_tooltip_text("How dark the area outside the focus is")
        self.dark_spin.connect("value-changed", self._on_dark_changed)
        self.dark_ctl.pack_start(self.dark_spin, False, False, 0)
        toolbar.pack_start(self.dark_ctl, False, False, 4)

        toolbar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)

        # color swatches
        for hexc in self.cfg["palette"]:
            sw = Gtk.Button()
            sw.set_size_request(24, 24)
            sw.set_tooltip_text(hexc)
            self._style_swatch(sw, hexc)
            sw.connect("clicked", self.on_color, hexc)
            toolbar.pack_start(sw, False, False, 0)
            self._hand_on_hover(sw)

        # custom color
        self.color_btn = Gtk.ColorButton()
        rgba = Gdk.RGBA(); rgba.parse(self.color)
        self.color_btn.set_rgba(rgba)
        self.color_btn.set_tooltip_text("Custom colour")
        self.color_btn.connect("color-set", self.on_custom_color)
        toolbar.pack_start(self.color_btn, False, False, 2)
        self._hand_on_hover(self.color_btn)

        # canvas in an overlay (so we can float a text entry on it)
        self.canvas = Gtk.DrawingArea()
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.canvas.connect("draw", self.on_canvas_draw)
        self.canvas.connect("button-press-event", self.on_canvas_press)
        self.canvas.connect("button-release-event", self.on_canvas_release)
        self.canvas.connect("motion-notify-event", self.on_canvas_motion)
        self.canvas.connect("realize",
                             lambda *_: self._set_canvas_cursor(self._tool_cursor()))

        self.overlay = Gtk.Overlay()
        self.overlay.add(self.canvas)
        self.overlay.connect("get-child-position", self._position_text_entry)
        root.pack_start(self.overlay, True, True, 0)

        # crop apply/cancel bar (hidden until crop drawn)
        self.crop_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.crop_bar.set_halign(Gtk.Align.CENTER)
        self.crop_bar.set_valign(Gtk.Align.END)
        self.crop_bar.set_margin_bottom(16)
        apply_b = Gtk.Button(label="Apply crop")
        apply_b.get_style_context().add_class("suggested-action")
        apply_b.connect("clicked", lambda *_: self.apply_crop())
        cancel_b = Gtk.Button(label="Cancel")
        cancel_b.connect("clicked", lambda *_: self.cancel_crop())
        self.crop_bar.pack_start(apply_b, False, False, 0)
        self.crop_bar.pack_start(cancel_b, False, False, 0)
        self.overlay.add_overlay(self.crop_bar)

        # everything is built now -> activate the default tool
        self.tool_buttons["arrow"].set_active(True)

    def _on_width_changed(self, spin):
        self.width = spin.get_value()
        sel = self.selected
        if sel is not None and hasattr(sel, "width"):
            self._push_undo()
            sel.width = self.width
            self.canvas.queue_draw()

    def _update_tool_controls(self):
        """Show the style control relevant to the active tool (thickness / font /
        blur / darkness)."""
        if getattr(self, "thick_ctl", None) is None:
            return
        t = self.tool
        self.blur_ctl.set_visible(t == "blur")
        self.dark_ctl.set_visible(t == "spotlight")
        self.font_ctl.set_visible(t == "text")
        self.thick_ctl.set_visible(t not in ("text", "blur", "spotlight"))

    def _on_font_changed(self, spin):
        self.font_size = spin.get_value()
        sel = self.selected
        if isinstance(sel, tools.Text):
            self._push_undo()
            sel.size = self.font_size
            self.canvas.queue_draw()

    def _on_blur_changed(self, spin):
        self.blur_block = int(spin.get_value())
        self._rebuild_surfaces()
        self.canvas.queue_draw()

    def _on_dark_changed(self, spin):
        self.spotlight_darkness = spin.get_value() / 100.0
        sel = self.selected
        if isinstance(sel, tools.Spotlight):
            self._push_undo()
            sel.darkness = self.spotlight_darkness
            self.canvas.queue_draw()

    def _style_swatch(self, btn, hexc):
        css = f"button {{ background: {hexc}; min-width:24px; min-height:24px; padding:0; }}".encode()
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        btn.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ----------------------------------------------------------- coord mapping
    def _layout(self):
        a = self.canvas.get_allocation()
        iw, ih = self.base_image.width, self.base_image.height
        scale = min(a.width / iw, a.height / ih) if iw and ih else 1
        ox = (a.width - iw * scale) / 2
        oy = (a.height - ih * scale) / 2
        return scale, ox, oy

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
                self.canvas.queue_draw()

    def _tool_cursor(self):
        """The canvas cursor for the active tool when not hovering a shape."""
        return {"crop": "crosshair", "select": "default"}.get(self.tool, "crosshair")

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
        self._apply_color_to_selected(hexc)

    def on_custom_color(self, btn):
        rgba = btn.get_rgba()
        self.color = "#%02x%02x%02x" % (int(rgba.red * 255), int(rgba.green * 255),
                                        int(rgba.blue * 255))
        self._apply_color_to_selected(self.color)

    def _apply_color_to_selected(self, hexc):
        if self.selected is not None and hasattr(self.selected, "color"):
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
        self.commit_text()
        ix, iy = self.to_image(ev.x, ev.y)
        self.press_img = (ix, iy)
        self.hover_ann = None
        t = self.tool

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
            self.selected = ann
            self.active_handle = None
            self._moving = True
            self._drag_last = (ix, iy)
            self._predrag = self._snapshot()
            self._drag_committed = False
            self.canvas.queue_draw()
            return True

        # --- Empty canvas: deselect, then the active tool draws/places. ---
        self.selected = None
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
        self.canvas.queue_draw()
        return True

    def on_canvas_motion(self, _w, ev):
        ix, iy = self.to_image(ev.x, ev.y)
        # a grab (move/resize) is in progress?
        if self.active_handle or self._moving:
            self._select_motion(ev.x, ev.y, ix, iy)
            return True
        # not pressing -> just update hover cursor/highlight
        if self.press_img is None:
            self._update_hover_cursor(ev.x, ev.y)
            return True
        x0, y0 = self.press_img
        t = self.tool
        if t in ("arrow", "line"):
            cls = tools.Arrow if t == "arrow" else tools.Line
            self.draft = cls(x0, y0, ix, iy, self.color, self.width)
        elif t in ("rect", "ellipse", "blur", "spotlight"):
            x, y = min(x0, ix), min(y0, iy)
            w, h = abs(ix - x0), abs(iy - y0)
            if t == "rect":
                self.draft = tools.Rect(x, y, w, h, self.color, self.width)
            elif t == "ellipse":
                self.draft = tools.Ellipse(x, y, w, h, self.color, self.width)
            elif t == "blur":
                self.draft = tools.Blur(x, y, w, h)
            else:
                self.draft = tools.Spotlight(x, y, w, h, self.spotlight_darkness)
        elif t in ("pen", "highlight") and self.draft:
            self.draft.points.append((ix, iy))
        elif t == "crop":
            x, y = min(x0, ix), min(y0, iy)
            self.crop_rect = (x, y, abs(ix - x0), abs(iy - y0))
        self.canvas.queue_draw()
        return True

    def on_canvas_release(self, _w, ev):
        if ev.button != 1:
            return False
        # finishing a grab (move/resize)?
        if self.active_handle or self._moving:
            self.active_handle = None
            self._moving = False
            self._drag_last = None
            self._predrag = None
            self.press_img = None
            return True
        t = self.tool
        if t == "crop":
            if self.crop_rect and self.crop_rect[2] > 4 and self.crop_rect[3] > 4:
                self.crop_bar.show()
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

    def _draft_is_meaningful(self):
        d = self.draft
        if isinstance(d, (tools.Pen, tools.Highlight)):
            return len(d.points) >= 2
        if isinstance(d, (tools.Rect, tools.Ellipse, tools.Blur, tools.Spotlight)):
            return d.w > 3 and d.h > 3
        if isinstance(d, (tools.Arrow, tools.Line)):
            return abs(d.x1 - d.x0) + abs(d.y1 - d.y0) > 4
        return True

    # ----------------------------------------------------------- select tool
    def _handle_points_widget(self, ann):
        """name -> (wx, wy) handle positions in widget space."""
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

    def _select_motion(self, wx, wy, ix, iy):
        if not (self.active_handle or self._moving) or self.selected is None:
            self._update_hover_cursor(wx, wy)
            return
        if not self._drag_committed:
            self.undo_stack.append(self._predrag)
            self.redo_stack.clear()
            self.dirty = True
            self._drag_committed = True
        sel = self.selected
        if self.active_handle:
            if sel.handle_style == "endpoints":
                sel.set_endpoint(self.active_handle, ix, iy)
            else:
                sel.set_bbox(*_resize_bbox(sel.bbox(), self.active_handle, ix, iy))
        elif self._moving:
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

    def delete_selected(self):
        if self.selected is not None and self.selected in self.annotations:
            self._push_undo()
            self.annotations.remove(self.selected)
            self.selected = None
            self.canvas.queue_draw()

    # ------------------------------------------------------------------ text
    def start_text(self, wx, wy, ix, iy):
        self.commit_text()
        self.text_pos = (wx, wy, ix, iy)
        entry = Gtk.Entry()
        entry.set_has_frame(True)
        sz = int(self.font_size)
        css = (f"entry {{ font-weight:bold; font-size:{sz}px; color:{self.color};"
               f" background: rgba(255,255,255,0.85); }}").encode()
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        entry.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        entry.connect("activate", lambda *_: self.commit_text())
        self.text_entry = entry
        self.overlay.add_overlay(entry)
        self.overlay.show_all()
        GLib.idle_add(entry.grab_focus)

    def _position_text_entry(self, _overlay, child, alloc):
        if child is self.text_entry and self.text_pos:
            wx, wy, _ix, _iy = self.text_pos
            alloc.x = int(wx)
            alloc.y = int(wy - self.font_size)
            nat = child.get_preferred_size()[1]
            alloc.width = max(160, nat.width)
            alloc.height = nat.height
            return True
        return False

    def commit_text(self):
        if not self.text_entry:
            return
        text = self.text_entry.get_text().strip()
        entry = self.text_entry
        self.text_entry = None
        if text and self.text_pos:
            _wx, _wy, ix, iy = self.text_pos
            self._push_undo()
            ann = tools.Text(ix, iy, text, self.color, self.font_size)
            self.annotations.append(ann)
            self.selected = ann  # auto-select so it can be moved/resized
        self.overlay.remove(entry)
        self.text_pos = None
        self.canvas.queue_draw()

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
        # checkerboard-ish neutral bg
        cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        scale, ox, oy = self._layout()
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        # base image
        cr.set_source_surface(self.base_surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        # committed annotations
        ctx = _DrawCtx(self.blur_surface, self.base_image.width, self.base_image.height)
        for ann in self.annotations:
            cr.save(); ann.draw(cr, ctx); cr.restore()
        # live draft
        if self.draft is not None:
            cr.save(); self.draft.draw(cr, ctx); cr.restore()
        cr.restore()
        # crop overlay (drawn in widget space)
        if self.tool == "crop" and self.crop_rect:
            self._draw_crop(cr, a)
            return False
        # hover highlight (any tool, when not the selected shape)
        if self.hover_ann is not None and self.hover_ann is not self.selected:
            self._draw_hover(cr, self.hover_ann)
        # selection handles (any tool)
        if self.selected is not None:
            self._draw_selection(cr)
        return False

    def _draw_hover(self, cr, ann):
        x, y, w, h = ann.bbox()
        wx, wy = self.to_widget(x, y)
        scale, _, _ = self._layout()
        pad = 3
        cr.set_source_rgba(*ACCENT, 0.55)
        cr.set_line_width(1.5)
        cr.rectangle(wx - pad, wy - pad, w * scale + 2 * pad, h * scale + 2 * pad)
        cr.stroke()

    def _draw_selection(self, cr):
        ann = self.selected
        x, y, w, h = ann.bbox()
        wx, wy = self.to_widget(x, y)
        scale, _, _ = self._layout()
        ww, wh = w * scale, h * scale
        cr.set_source_rgba(*ACCENT, 0.9)
        cr.set_line_width(1.5)
        cr.set_dash([4, 3])
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
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        k = ev.keyval
        if k == Gdk.KEY_Escape:
            if self.text_entry:
                self.text_entry.set_text(""); self.commit_text()
            elif self.crop_rect:
                self.cancel_crop()
            elif self.selected is not None:
                self.selected = None; self.canvas.queue_draw()
            else:
                self._request_close()
            return True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.crop_rect and not self.text_entry:
            self.apply_crop(); return True
        if k in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and not self.text_entry \
                and self.selected is not None:
            self.delete_selected(); return True
        if ctrl and k in (Gdk.KEY_z, Gdk.KEY_Z):
            self.redo() if shift else self.undo(); return True
        if ctrl and k in (Gdk.KEY_y, Gdk.KEY_Y):
            self.redo(); return True
        if ctrl and k in (Gdk.KEY_c, Gdk.KEY_C):
            self.do_copy(); return True
        if ctrl and k in (Gdk.KEY_s, Gdk.KEY_S):
            self.do_save(); return True
        if ctrl and k in (Gdk.KEY_u, Gdk.KEY_U):
            self.do_upload(); return True
        # single-key tool shortcuts
        keymap = {Gdk.KEY_v: "select", Gdk.KEY_a: "arrow", Gdk.KEY_r: "rect",
                  Gdk.KEY_e: "ellipse", Gdk.KEY_l: "line", Gdk.KEY_p: "pen",
                  Gdk.KEY_h: "highlight", Gdk.KEY_t: "text", Gdk.KEY_b: "blur",
                  Gdk.KEY_o: "spotlight", Gdk.KEY_n: "counter", Gdk.KEY_x: "crop"}
        if not ctrl and k in keymap and not self.text_entry:
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

    def _request_close(self):
        if self.dirty and not self._closing:
            self._confirm_close()
        else:
            self.destroy()

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
        return export.render(self.base_surface, self.blur_surface, self.annotations)

    def do_copy(self):
        surface = self._render()
        ok = export.copy_to_clipboard(surface)
        export.notify("Copied to clipboard" if ok else "Copy failed")
        self._closing = True
        self.destroy()

    def do_save(self):
        surface = self._render()
        path = export.save_to_disk(surface, self.cfg)
        if self.cfg.get("copy_on_save"):
            export.copy_to_clipboard(surface)
        export.notify("Screenshot saved", path, path)
        self._closing = True
        self.destroy()

    def do_upload(self):
        import threading
        from . import upload
        surface = self._render()
        data = export.surface_to_png_bytes(surface)
        self.upload_b.set_sensitive(False)
        self.upload_b.set_label("Uploading…")
        export.notify("Uploading screenshot…")

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

    def do_pin(self):
        # Render now, then close; app.py launches the pin window in a fresh loop.
        self.pending_pin = self._render()
        if self.cfg.get("copy_on_save"):
            export.copy_to_clipboard(self.pending_pin)
        self._closing = True
        self.destroy()

    def run(self):
        """Show the editor; returns the surface to pin (or None)."""
        self.pending_pin = None
        self.show_all()
        self.crop_bar.hide()
        self._update_tool_controls()  # hide contextual controls show_all revealed
        Gtk.main()
        return self.pending_pin
