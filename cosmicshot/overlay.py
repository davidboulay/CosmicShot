"""Full-screen dimmed region selector (CleanShot-style).

Captures the whole desktop first, then paints it under a dim layer-shell overlay
on each monitor. The user drags a rectangle; we return it in image-pixel space.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell, GLib  # noqa: E402
import cairo  # noqa: E402

from .capture import desktop_bounds

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
            if self.controller.preseeded and not self.controller.dragging:
                self._draw_hint(cr, a, "↵ Reuse last region  ·  drag for a new one  ·  Esc")
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

    def __init__(self, screenshot_path, monitors, last_region=None):
        self.surface = cairo.ImageSurface.create_from_png(screenshot_path)
        self.monitors = monitors
        self.windows = []
        self.dragging = False
        self.start = None
        self.cur = None
        self.result = None  # (x, y, w, h) in image-pixel space
        self._cancelled = False
        self.preseeded = False
        # Pre-load the previously used region (CleanShot "capture last area").
        if last_region:
            x, y, w, h = last_region
            dx0, dy0, dx1, dy1 = desktop_bounds(monitors)
            if (w >= 4 and h >= 4 and x >= dx0 and y >= dy0
                    and x + w <= dx1 and y + h <= dy1):
                self.start = (x, y)
                self.cur = (x + w, y + h)
                self.preseeded = True

    def begin(self, _win, ix, iy):
        self.dragging = True
        self.preseeded = False  # user is drawing a fresh region
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
