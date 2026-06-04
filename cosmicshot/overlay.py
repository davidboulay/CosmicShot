"""Full-screen dimmed region selector (CleanShot-style).

Captures the whole desktop first, then paints it under a dim layer-shell overlay
on each monitor. The user drags a rectangle; we return it in image-pixel space.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell, GLib  # noqa: E402
import cairo  # noqa: E402

DIM = (0, 0, 0, 0.45)
ACCENT = (0.0, 0.48, 1.0)  # selection border


class _MonitorOverlay(Gtk.Window):
    def __init__(self, controller, monitor, gdk_monitor, surface):
        super().__init__()
        self.controller = controller
        self.m = monitor
        self.surface = surface  # full-desktop cairo surface (image-pixel space)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_monitor(self, gdk_monitor)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                     GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("key-press-event", self.on_key)

        self.pointer = None  # widget coords for crosshair

    # --- coordinate helpers (widget <-> image pixel space) ---
    def _scale(self):
        a = self.area.get_allocation()
        sx = a.width / self.m.width if self.m.width else 1
        sy = a.height / self.m.height if self.m.height else 1
        return sx or 1, sy or 1

    def to_image(self, wx, wy):
        sx, sy = self._scale()
        return self.m.x + wx / sx, self.m.y + wy / sy

    def to_widget(self, ix, iy):
        sx, sy = self._scale()
        return (ix - self.m.x) * sx, (iy - self.m.y) * sy

    # --- input ---
    def on_press(self, _w, ev):
        if ev.button == 1:
            self.controller.begin(self, *self.to_image(ev.x, ev.y))
        return True

    def on_motion(self, _w, ev):
        self.pointer = (ev.x, ev.y)
        if self.controller.dragging:
            self.controller.update(*self.to_image(ev.x, ev.y))
        self.controller.queue_redraw()
        return True

    def on_release(self, _w, ev):
        if ev.button == 1 and self.controller.dragging:
            self.controller.update(*self.to_image(ev.x, ev.y))
            self.controller.finish()
        return True

    def on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.controller.cancel()
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.controller.finish()  # confirm current (e.g. reused) selection
        return True

    # --- drawing ---
    def on_draw(self, _w, cr):
        a = self.area.get_allocation()
        sx, sy = self._scale()

        # 1. base screenshot for this monitor's slice
        cr.save()
        cr.scale(sx, sy)
        cr.set_source_surface(self.surface, -self.m.x, -self.m.y)
        cr.get_source().set_filter(cairo.FILTER_FAST)
        cr.paint()
        cr.restore()

        # 2. dim everything
        cr.set_source_rgba(*DIM)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()

        sel = self.controller.selection_image_rect()
        if sel:
            ix, iy, iw, ih = sel
            wx, wy = self.to_widget(ix, iy)
            ww, wh = iw * sx, ih * sy
            # 3. punch through the dim -> show bright screenshot inside selection
            cr.save()
            cr.rectangle(wx, wy, ww, wh)
            cr.clip()
            cr.scale(sx, sy)
            cr.set_source_surface(self.surface, -self.m.x, -self.m.y)
            cr.paint()
            cr.restore()
            # 4. border + handles + dimensions
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(2)
            cr.rectangle(wx, wy, ww, wh)
            cr.stroke()
            self._draw_handles(cr, wx, wy, ww, wh)
            self._draw_dims(cr, wx, wy, ww, wh, iw, ih)
        elif self.pointer:
            # crosshair before first drag
            cr.set_source_rgba(1, 1, 1, 0.6)
            cr.set_line_width(1)
            px, py = self.pointer
            cr.move_to(0, py); cr.line_to(a.width, py)
            cr.move_to(px, 0); cr.line_to(px, a.height)
            cr.stroke()
            self._draw_hint(cr, a)
        return False

    def _draw_handles(self, cr, x, y, w, h):
        r = 4
        cr.set_source_rgb(1, 1, 1)
        for hx, hy in [(x, y), (x + w / 2, y), (x + w, y),
                       (x, y + h / 2), (x + w, y + h / 2),
                       (x, y + h), (x + w / 2, y + h), (x + w, y + h)]:
            cr.arc(hx, hy, r, 0, 2 * 3.14159)
            cr.fill_preserve()
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(1.5)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)

    def _draw_dims(self, cr, x, y, w, h, iw, ih):
        label = f"{int(iw)} × {int(ih)}"
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(13)
        ext = cr.text_extents(label)
        pad = 6
        bx = x
        by = y - ext.height - 2 * pad - 4
        if by < 2:
            by = y + 4
        cr.set_source_rgba(0, 0, 0, 0.75)
        self._round_rect(cr, bx, by, ext.width + 2 * pad, ext.height + 2 * pad, 4)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(bx + pad, by + pad + ext.height)
        cr.show_text(label)

    def _draw_hint(self, cr, a, text="Drag to select  ·  Esc to cancel"):
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(14)
        ext = cr.text_extents(text)
        pad = 12
        bw, bh = ext.width + 2 * pad, ext.height + 2 * pad
        bx = (a.width - bw) / 2
        by = a.height - bh - 48
        cr.set_source_rgba(0, 0, 0, 0.7)
        self._round_rect(cr, bx, by, bw, bh, 8)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(bx + pad, by + pad + ext.height)
        cr.show_text(text)

    @staticmethod
    def _round_rect(cr, x, y, w, h, r):
        import math
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()


class SelectionOverlay:
    """Runs the overlay across all monitors and returns the chosen rect."""

    def __init__(self, screenshot_path, monitors):
        self.surface = cairo.ImageSurface.create_from_png(screenshot_path)
        self.monitors = monitors
        self.windows = []
        self.dragging = False
        self.start = None
        self.cur = None
        self.result = None  # (x, y, w, h) in image-pixel space
        self._cancelled = False

    def begin(self, _win, ix, iy):
        self.dragging = True
        self.start = (ix, iy)
        self.cur = (ix, iy)

    def update(self, ix, iy):
        self.cur = (ix, iy)

    def selection_image_rect(self):
        if not self.start or not self.cur:
            return None
        x0, y0 = self.start
        x1, y1 = self.cur
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 1 or h < 1:
            return None
        return (x, y, w, h)

    def queue_redraw(self):
        for win in self.windows:
            win.area.queue_draw()

    def finish(self):
        rect = self.selection_image_rect()
        self.dragging = False
        if rect and rect[2] >= 4 and rect[3] >= 4:
            self.result = tuple(int(round(v)) for v in rect)
            self._quit()
        else:
            # too small -> reset, let user try again
            self.start = self.cur = None
            self.queue_redraw()

    def cancel(self):
        self._cancelled = True
        self.result = None
        self._quit()

    def _quit(self):
        for win in self.windows:
            win.destroy()
        GLib.idle_add(Gtk.main_quit)

    def run(self):
        display = Gdk.Display.get_default()
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            win = _MonitorOverlay(self, m, gm, self.surface)
            self.windows.append(win)
            win.show_all()
        Gtk.main()
        return self.result


class _ScreenPickWindow(Gtk.Window):
    """One layer-shell window per monitor; lights up its screen on hover."""

    def __init__(self, controller, monitor, gdk_monitor, surface):
        super().__init__()
        self.controller = controller
        self.m = monitor
        self.surface = surface

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_monitor(self, gdk_monitor)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                     GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("enter-notify-event", self.on_enter)
        self.connect("key-press-event", self.on_key)

    def on_enter(self, _w, _ev):
        self.controller.set_hover(self.m.index)
        return False

    def on_motion(self, _w, _ev):
        self.controller.set_hover(self.m.index)
        return True

    def on_press(self, _w, ev):
        if ev.button == 1:
            self.controller.choose(self.m)
        return True

    def on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.controller.cancel()
        return True

    def _scale(self):
        a = self.area.get_allocation()
        sx = a.width / self.m.width if self.m.width else 1
        sy = a.height / self.m.height if self.m.height else 1
        return sx or 1, sy or 1

    def on_draw(self, _w, cr):
        a = self.area.get_allocation()
        sx, sy = self._scale()
        cr.save()
        cr.scale(sx, sy)
        cr.set_source_surface(self.surface, -self.m.x, -self.m.y)
        cr.get_source().set_filter(cairo.FILTER_FAST)
        cr.paint()
        cr.restore()

        hovered = self.controller.hover == self.m.index
        if not hovered:
            cr.set_source_rgba(*DIM)
            cr.rectangle(0, 0, a.width, a.height)
            cr.fill()
        else:
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(6)
            cr.rectangle(3, 3, a.width - 6, a.height - 6)
            cr.stroke()
        self._draw_label(cr, a, hovered)
        return False

    def _draw_label(self, cr, a, hovered):
        label = f"{self.m.model}   {self.m.width} × {self.m.height}"
        if hovered:
            label = "Click to capture  ·  " + label
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(16)
        ext = cr.text_extents(label)
        pad = 12
        bw, bh = ext.width + 2 * pad, ext.height + 2 * pad
        bx = (a.width - bw) / 2
        by = (a.height - bh) / 2
        cr.set_source_rgba(0, 0, 0, 0.78)
        _MonitorOverlay._round_rect(cr, bx, by, bw, bh, 10)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(bx + pad, by + pad + ext.height)
        cr.show_text(label)


class _WindowPickWindow(Gtk.Window):
    """Per-monitor layer-shell overlay that highlights the hovered app window."""

    def __init__(self, controller, monitor, gdk_monitor, surface):
        super().__init__()
        self.controller = controller
        self.m = monitor
        self.surface = surface

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_monitor(self, gdk_monitor)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                     GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK)
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("key-press-event", self.on_key)

    def _scale(self):
        a = self.area.get_allocation()
        return (a.width / self.m.width if self.m.width else 1) or 1, \
               (a.height / self.m.height if self.m.height else 1) or 1

    def on_motion(self, _w, ev):
        sx, sy = self._scale()
        self.controller.set_point(self.m.x + ev.x / sx, self.m.y + ev.y / sy)
        return True

    def on_press(self, _w, ev):
        if ev.button == 1:
            self.controller.choose()
        return True

    def on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.controller.cancel()
        return True

    def on_draw(self, _w, cr):
        a = self.area.get_allocation()
        sx, sy = self._scale()
        cr.save(); cr.scale(sx, sy)
        cr.set_source_surface(self.surface, -self.m.x, -self.m.y)
        cr.get_source().set_filter(cairo.FILTER_FAST); cr.paint()
        cr.restore()
        cr.set_source_rgba(*DIM); cr.rectangle(0, 0, a.width, a.height); cr.fill()

        win = self.controller.hovered
        if win:
            wx, wy = (win["x"] - self.m.x) * sx, (win["y"] - self.m.y) * sy
            ww, wh = win["w"] * sx, win["h"] * sy
            cr.save()                       # punch the window region bright
            cr.rectangle(wx, wy, ww, wh); cr.clip()
            cr.scale(sx, sy)
            cr.set_source_surface(self.surface, -self.m.x, -self.m.y); cr.paint()
            cr.restore()
            cr.set_source_rgb(*ACCENT); cr.set_line_width(3)
            cr.rectangle(wx, wy, ww, wh); cr.stroke()
            self._draw_label(cr, win, wx, wy, ww)
        return False

    def _draw_label(self, cr, win, wx, wy, ww):
        name = win.get("app_id") or win.get("title") or "window"
        label = f"{name}   {int(win['w'])} × {int(win['h'])}"
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14)
        ext = cr.text_extents(label); pad = 10
        bw, bh = ext.width + 2 * pad, ext.height + 2 * pad
        bx = wx + max(0, (ww - bw) / 2)
        by = wy + 8
        cr.set_source_rgba(0, 0, 0, 0.8)
        _MonitorOverlay._round_rect(cr, bx, by, bw, bh, 8); cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(bx + pad, by + pad + ext.height); cr.show_text(label)


class WindowPicker:
    """Dim everything; the app window under the pointer lights up. Click it to
    capture that whole window. Returns (x, y, w, h) global rect, or None."""

    def __init__(self, screenshot_path, monitors, windows):
        self.surface = cairo.ImageSurface.create_from_png(screenshot_path)
        self.monitors = monitors
        self.windows = windows
        self.hovered = None
        self.windows_widgets = []
        self.result = None

    def _window_at(self, gx, gy):
        # COSMIC doesn't expose reliable z-order, so among the windows covering
        # this point we pick the front-most by heuristic: the focused (active)
        # window if it's here, otherwise the smallest one — a window on top is
        # usually the focused and/or smaller one. Minimized windows were already
        # dropped, so they're never offered.
        covering = [w for w in self.windows
                    if w["x"] <= gx <= w["x"] + w["w"]
                    and w["y"] <= gy <= w["y"] + w["h"]]
        if not covering:
            return None
        active = [w for w in covering if w.get("active")]
        pool = active or covering
        return min(pool, key=lambda w: w["w"] * w["h"])

    def set_point(self, gx, gy):
        win = self._window_at(gx, gy)
        if win is not self.hovered:
            self.hovered = win
            for w in self.windows_widgets:
                w.area.queue_draw()

    def choose(self):
        if self.hovered:
            w = self.hovered
            self.result = (int(w["x"]), int(w["y"]), int(w["w"]), int(w["h"]))
            self._quit()

    def cancel(self):
        self.result = None
        self._quit()

    def _quit(self):
        for w in self.windows_widgets:
            w.destroy()
        GLib.idle_add(Gtk.main_quit)

    def run(self):
        display = Gdk.Display.get_default()
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            win = _WindowPickWindow(self, m, gm, self.surface)
            self.windows_widgets.append(win)
            win.show_all()
        Gtk.main()
        return self.result


class _DimWindow(Gtk.Window):
    """Per-monitor dim layer with a transparent hole over the capture region, so
    the user focuses on the area being scrolled. Click-through (empty input
    region) so scroll events reach the real window underneath; the transparent
    hole means the screenshot grab still sees the live content there."""

    def __init__(self, monitor, gdk_monitor, region):
        super().__init__()
        self.m = monitor
        self.region = region
        self.set_app_paintable(True)
        screen = self.get_screen()
        vis = screen.get_rgba_visual() if screen else None
        if vis:
            self.set_visual(vis)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_monitor(self, gdk_monitor)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                     GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, -1)
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.connect("realize", self._make_click_through)

    def _make_click_through(self, _w):
        win = self.get_window()
        if win is not None:
            win.input_shape_combine_region(cairo.Region(), 0, 0)  # empty = pass all

    def _draw(self, _w, cr):
        a = self.area.get_allocation()
        sx = a.width / self.m.width if self.m.width else 1
        sy = a.height / self.m.height if self.m.height else 1
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        # punch the region hole (in this monitor's local coords)
        rx, ry, rw, rh = self.region
        lx, ly = (rx - self.m.x) * sx, (ry - self.m.y) * sy
        lw, lh = rw * sx, rh * sy
        cr.set_source_rgba(0, 0, 0, 0)
        cr.rectangle(lx, ly, lw, lh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(2)
        cr.rectangle(lx, ly, lw, lh)
        cr.stroke()
        return False


class ScrollCapture:
    """Manual-scroll capture. A region has been chosen; dim everything else,
    grab frames of the region on a background thread while the user scrolls, and
    return the frames to stitch. If the user scrolls too fast (consecutive
    frames stop overlapping) the result can't be stitched, so we flag it and
    require a cancel + retry. Returns [] if cancelled / too fast."""

    def __init__(self, region, monitors, capture_mod):
        self.region = region
        self.monitors = monitors
        self._capture = capture_mod
        self.frames = []
        self._running = False
        self._cancelled = False
        self._too_fast = False
        self._thread = None
        self.dims = []
        self.bar = None
        self.status = None
        self.done_btn = None

    def _build_bar(self):
        win = Gtk.Window()
        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(win, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(win, GtkLayerShell.Edge.BOTTOM, 48)
        GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.get_style_context().add_class("scroll-bar")
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(16); box.set_margin_end(16)
        win.add(box)
        self.status = Gtk.Label()
        self.status.set_markup("Scroll down slowly…  <b>0</b> frames")
        box.pack_start(self.status, False, False, 0)
        self.done_btn = Gtk.Button(label="Done (↵)")
        self.done_btn.connect("clicked", lambda _b: self._finish())
        box.pack_start(self.done_btn, False, False, 0)
        cancel = Gtk.Button(label="Cancel (Esc)")
        cancel.connect("clicked", lambda _b: self._cancel())
        box.pack_start(cancel, False, False, 0)
        win.connect("key-press-event", self._on_key)
        self.bar = win

    def _on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self._cancel()
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self._too_fast:
            self._finish()
        return True

    def _set_status(self, n):
        if self._too_fast:
            self.status.set_markup(
                "<span foreground='#ff5c5c'><b>Too fast — Cancel and retry, "
                "scrolling slower.</b></span>")
            self.done_btn.set_sensitive(False)
        else:
            self.status.set_markup(f"Scroll down slowly…  <b>{n}</b> frames")
        return False

    def _worker(self):
        import time
        from PIL import Image
        from . import scroll as _scroll
        x, y, w, h = self.region
        last_kept = None
        while self._running:
            try:
                shot = self._capture.full()
                full = Image.open(shot).convert("RGB")
                frame = full.crop((max(0, x), max(0, y),
                                   min(x + w, full.width), min(y + h, full.height)))
            except Exception:
                time.sleep(0.1)
                continue
            if last_kept is None:
                self.frames.append(frame); last_kept = frame
                GLib.idle_add(self._set_status, len(self.frames))
            elif _scroll.changed(last_kept, frame):
                shift, err = _scroll.detect_overlap(last_kept, frame)
                if shift >= 4 and _scroll.is_confident(err):
                    self.frames.append(frame); last_kept = frame
                    GLib.idle_add(self._set_status, len(self.frames))
                else:
                    # view moved but no overlap with the last good frame: a gap.
                    self._too_fast = True
                    GLib.idle_add(self._set_status, len(self.frames))
            time.sleep(0.12)

    def _finish(self):
        self._running = False
        self._teardown()

    def _cancel(self):
        self._running = False
        self._cancelled = True
        self._teardown()

    def _teardown(self):
        for d in self.dims:
            d.destroy()
        if self.bar is not None:
            self.bar.destroy()
        GLib.idle_add(Gtk.main_quit)

    def run(self):
        import threading
        display = Gdk.Display.get_default()
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            d = _DimWindow(m, gm, self.region)
            self.dims.append(d)
            d.show_all()
        self._build_bar()
        self.bar.show_all()
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        Gtk.main()
        if self._thread:
            self._thread.join(timeout=2)
        if self._cancelled or self._too_fast:
            return []
        return self.frames


class ScreenPicker:
    """Dim every monitor; the one under the pointer lights up. Click it to pick
    that whole screen. Returns the chosen Monitor, or None if cancelled."""

    def __init__(self, screenshot_path, monitors):
        self.surface = cairo.ImageSurface.create_from_png(screenshot_path)
        self.monitors = monitors
        self.windows = []
        self.hover = -1
        self.result = None

    def set_hover(self, index):
        if self.hover != index:
            self.hover = index
            for win in self.windows:
                win.area.queue_draw()

    def choose(self, monitor):
        self.result = monitor
        self._quit()

    def cancel(self):
        self.result = None
        self._quit()

    def _quit(self):
        for win in self.windows:
            win.destroy()
        GLib.idle_add(Gtk.main_quit)

    def run(self):
        display = Gdk.Display.get_default()
        # Pre-hover the monitor under the pointer so a single screen is obvious.
        from .capture import monitor_at_pointer
        try:
            self.hover = monitor_at_pointer(self.monitors).index
        except Exception:
            self.hover = self.monitors[0].index if self.monitors else -1
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            win = _ScreenPickWindow(self, m, gm, self.surface)
            self.windows.append(win)
            win.show_all()
        Gtk.main()
        return self.result
