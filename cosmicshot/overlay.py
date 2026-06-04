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
    """Per-monitor dim layer with a transparent hole over the capture region.
    Purely VISUAL and pointer click-through, so scrolling reaches the window
    underneath and the grab sees live content through the hole. It never takes
    keyboard or pointer input — all controls live on the separate _ControlBar,
    which is a normal (non-click-through) window so it always works."""

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
        # No keyboard for the dim layer (the control bar owns input).
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.connect("realize", self._make_click_through)
        self.connect("map-event", self._make_click_through)

    def _make_click_through(self, *_):
        self.input_shape_combine_region(cairo.Region())
        win = self.get_window()
        if win is not None:
            win.input_shape_combine_region(cairo.Region(), 0, 0)

    def _draw(self, _w, cr):
        a = self.area.get_allocation()
        sx = a.width / self.m.width if self.m.width else 1
        sy = a.height / self.m.height if self.m.height else 1
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        rx, ry, rw, rh = self.region
        lx, ly = (rx - self.m.x) * sx, (ry - self.m.y) * sy
        lw, lh = rw * sx, rh * sy
        cr.set_source_rgba(0, 0, 0, 0)            # transparent hole = the region
        cr.rectangle(lx, ly, lw, lh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_rgb(*ACCENT)                # border OUTSIDE the hole
        cr.set_line_width(2)
        cr.rectangle(lx - 2, ly - 2, lw + 4, lh + 4)
        cr.stroke()
        return False


class _ControlBar(Gtk.Window):
    """Always-visible, clickable control with Done/Cancel + status. A NORMAL
    layer-shell window (not click-through) with EXCLUSIVE keyboard, so Esc, Enter
    and the buttons always work — there is always a way out."""

    def __init__(self, controller, gdk_monitor=None, anchor_top=False):
        super().__init__()
        self.controller = controller
        GtkLayerShell.init_for_window(self)
        if gdk_monitor is not None:
            GtkLayerShell.set_monitor(self, gdk_monitor)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        # Sit clear of the capture region: above it if the region reaches the
        # bottom of its screen, otherwise below it.
        edge = GtkLayerShell.Edge.TOP if anchor_top else GtkLayerShell.Edge.BOTTOM
        GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_margin(self, edge, 40)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(16); box.set_margin_end(16)
        box.get_style_context().add_class("scroll-bar")
        self.add(box)
        self.status = Gtk.Label(label="Scroll down slowly…  0 frames")
        box.pack_start(self.status, False, False, 0)
        self.done = Gtk.Button(label="Done (↵)")
        self.done.connect("clicked", lambda _b: self.controller.finish())
        box.pack_start(self.done, False, False, 0)
        cancel = Gtk.Button(label="Cancel (Esc)")
        cancel.connect("clicked", lambda _b: self.controller.cancel())
        box.pack_start(cancel, False, False, 0)
        self.connect("key-press-event", self._on_key)

    def _on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.controller.cancel()
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.controller.finish()
        return True

    def set_status(self, text, too_fast=False):
        if too_fast:
            self.status.set_markup(
                "<span foreground='#ff5c5c'><b>" + text + "</b></span>")
            self.done.set_sensitive(False)
        else:
            self.status.set_text(text)


class AutoScrollCapture:
    """Hands-free scrolling capture. Dims everything except the region, then a
    virtual mouse (uinput) scrolls the window under the pointer ITSELF: first up
    to the top, then down in controlled steps, grabbing and stitching as it
    goes. The user doesn't scroll (and shouldn't); Esc / Cancel aborts at any
    time and a watchdog is a final backstop. Returns frames to stitch, or []."""

    WATCHDOG_S = 180
    MAX_FRAMES = 300

    def __init__(self, region, monitors, capture_mod):
        self.region = region
        self.monitors = monitors
        self._capture = capture_mod
        self.frames = []
        self._running = False
        self._cancelled = False
        self.too_fast = False          # never happens here (we control the step)
        self._thread = None
        self.dims = []
        self.ctrl = None

    def _status(self, text):
        if self.ctrl:
            self.ctrl.set_status(text)
        return False

    def _grab(self):
        # Hide the control bar for the instant of the grab so it's never in the
        # frame (full-window regions leave nowhere to put it off-region). The
        # bar is visible the rest of the time (scrolling/settling) so Stop/Esc
        # stay responsive.
        import threading as _t
        import time
        from PIL import Image
        ev = _t.Event()
        GLib.idle_add(lambda: (self.ctrl.hide(), ev.set(), False)[2])
        ev.wait(0.3)
        time.sleep(0.05)
        try:
            shot = self._capture.full()
            full = Image.open(shot).convert("RGB")
            x, y, w, h = self.region
            frame = full.crop((max(0, x), max(0, y),
                               min(x + w, full.width), min(y + h, full.height)))
        except Exception:
            frame = None
        GLib.idle_add(lambda: (self.ctrl.show(), False)[1])
        return frame

    def _desktop_bounds(self):
        x0 = min(m.x for m in self.monitors)
        y0 = min(m.y for m in self.monitors)
        x1 = max(m.x + m.width for m in self.monitors)
        y1 = max(m.y + m.height for m in self.monitors)
        return (x0, y0, x1 - x0, y1 - y0)

    def _worker(self):
        import time
        from . import inject, scroll as _scroll
        rx, ry, rw, vh = self.region
        center = (rx + rw / 2, ry + vh / 2)
        try:
            # Absolute positioning: park the pointer over the target so scrolling
            # is independent of the user's real mouse.
            scroller = inject.Scroller(desktop=self._desktop_bounds())
        except Exception:
            self._cancelled = True
            GLib.idle_add(self._teardown)
            return

        def step(ticks, settle):
            scroller.scroll(ticks)
            time.sleep(settle)
            return self._grab()

        # Park the pointer over the target once (not every step — re-asserting
        # would fight the user reaching for Stop). The pointer then stays put.
        scroller.move_to(*center)

        # Phase 1: fast rewind to the top (big upward bursts until no movement).
        GLib.idle_add(self._status, "Rewinding to top…")
        prev = self._grab()
        stable = 0
        for _ in range(80):
            if not self._running:
                break
            cur = step(24, 0.22)             # large up burst
            if cur is None:
                continue
            s, _e = _scroll.detect_overlap(cur, prev)   # cur->prev moved up = we went up
            if prev is not None and s < 4 and not _scroll.changed(prev, cur):
                stable += 1
                if stable >= 2:
                    break                    # at the top
            else:
                stable = 0
            prev = cur

        # Phase 2: capture downward. Steps stop at the bottom (no vertical shift).
        self.frames = []
        last = self._grab()
        if last is not None:
            self.frames.append(last)
        GLib.idle_add(self._status, f"Capturing…  {len(self.frames)} frames")
        ticks, stall = 8, 0
        while self._running and len(self.frames) < self.MAX_FRAMES:
            cur = step(ticks, 0.34)
            if cur is None:
                continue
            s, err = _scroll.detect_overlap(last, cur)
            if s < 4:                         # no vertical movement -> bottom
                stall += 1
                if stall >= 2:
                    break
                ticks = min(ticks + 4, 30)    # nudge harder in case it was stuck
                continue
            stall = 0
            if _scroll.is_confident(err):
                self.frames.append(cur); last = cur
                GLib.idle_add(self._status, f"Capturing…  {len(self.frames)} frames")
                if s < 0.45 * vh:
                    ticks = min(ticks + 3, 30)   # speed up if steps are small
                elif s > 0.75 * vh:
                    ticks = max(2, ticks - 3)    # ease off near no-overlap
            else:
                ticks = max(2, ticks - 3)        # overshoot/animation: slow down

        scroller.close()
        self._running = False
        GLib.idle_add(self._teardown)

    def finish(self):
        self._running = False

    def cancel(self):
        self._cancelled = True
        self._running = False

    def _teardown(self):
        for d in self.dims:
            d.destroy()
        if self.ctrl is not None:
            self.ctrl.destroy()
        Gtk.main_quit()
        return False

    def _watchdog(self):
        if self._running:
            self._cancelled = True
            self._running = False
            self._teardown()
        return False

    def run(self):
        import threading
        display = Gdk.Display.get_default()
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            d = _DimWindow(m, gm, self.region)
            self.dims.append(d)
            d.show_all()
        rx, ry, rw, rh = self.region
        cx, cy = rx + rw / 2, ry + rh / 2
        host = next((m for m in self.monitors
                     if m.x <= cx < m.x + m.width and m.y <= cy < m.y + m.height),
                    self.monitors[0])
        anchor_top = (host.y + host.height) - (ry + rh) < 120
        self.ctrl = _ControlBar(self, display.get_monitor(host.index), anchor_top)
        # "Stop & Save" ends the auto-scroll early and keeps what's captured.
        self.ctrl.done.set_label("Stop & Save")
        self.ctrl.set_status("Auto-scrolling…")
        self.ctrl.show_all()
        self._running = True
        GLib.timeout_add_seconds(self.WATCHDOG_S, self._watchdog)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        Gtk.main()
        if self._thread:
            self._thread.join(timeout=2)
        return [] if self._cancelled else self.frames


class ScrollCapture:
    """Manual-scroll capture (fallback when input injection is unavailable).
    Dim everything except the region (click-through, visual only); an
    always-clickable control bar drives Done/Cancel. Frames are grabbed on a
    background thread while the user scrolls down; up-scroll is ignored; a
    downward gap (scrolled too fast) is flagged and requires retry. A watchdog
    auto-cancels after WATCHDOG_S so the user can never be trapped."""

    WATCHDOG_S = 180

    def __init__(self, region, monitors, capture_mod):
        self.region = region
        self.monitors = monitors
        self._capture = capture_mod
        self.frames = []
        self._running = False
        self._cancelled = False
        self.too_fast = False
        self._thread = None
        self.dims = []
        self.ctrl = None

    def _update_status(self):
        if self.too_fast:
            self.ctrl.set_status("Too fast — press Esc / Cancel and retry slower",
                                 too_fast=True)
        else:
            self.ctrl.set_status(
                f"Scroll down slowly…  {len(self.frames)} frames")
        return False

    def _grab_region(self):
        """Grab the desktop and crop to the region. The control bar stays
        visible (positioned off the region) so it's always responsive."""
        from PIL import Image
        try:
            shot = self._capture.full()
            full = Image.open(shot).convert("RGB")
            x, y, w, h = self.region
            return full.crop((max(0, x), max(0, y),
                              min(x + w, full.width), min(y + h, full.height)))
        except Exception:
            return None

    def _worker(self):
        import time
        from . import scroll as _scroll
        last_kept = None
        while self._running:
            frame = self._grab_region()
            if frame is None:
                time.sleep(0.1); continue
            if last_kept is None:
                self.frames.append(frame); last_kept = frame
                GLib.idle_add(self._update_status)
            elif _scroll.changed(last_kept, frame):
                d_s, d_e = _scroll.detect_overlap(last_kept, frame)
                u_s, u_e = _scroll.detect_overlap(frame, last_kept)
                if _scroll.is_confident(d_e) and d_e <= u_e and d_s >= 4:
                    self.frames.append(frame); last_kept = frame
                    GLib.idle_add(self._update_status)
                elif _scroll.is_confident(u_e):
                    pass                       # scrolled up — ignore
                else:
                    self.too_fast = True
                    GLib.idle_add(self._update_status)
            time.sleep(0.18)

    def finish(self):
        self._running = False
        self._teardown()

    def cancel(self):
        self._running = False
        self._cancelled = True
        self._teardown()

    def _teardown(self):
        for d in self.dims:
            d.destroy()
        if self.ctrl is not None:
            self.ctrl.destroy()
        GLib.idle_add(Gtk.main_quit)

    def run(self):
        import threading
        display = Gdk.Display.get_default()
        for m in self.monitors:
            gm = display.get_monitor(m.index)
            d = _DimWindow(m, gm, self.region)
            self.dims.append(d)
            d.show_all()
        # Put the control bar on the region's monitor, clear of the region:
        # below it if there's room, otherwise above it.
        rx, ry, rw, rh = self.region
        cx, cy = rx + rw / 2, ry + rh / 2
        host = next((m for m in self.monitors
                     if m.x <= cx < m.x + m.width and m.y <= cy < m.y + m.height),
                    self.monitors[0])
        room_below = (host.y + host.height) - (ry + rh)
        anchor_top = room_below < 120
        self.ctrl = _ControlBar(self, display.get_monitor(host.index), anchor_top)
        self.ctrl.show_all()
        self._running = True
        # Last-resort escape: never let the capture trap the user.
        GLib.timeout_add_seconds(self.WATCHDOG_S, self._watchdog)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        Gtk.main()
        if self._thread:
            self._thread.join(timeout=2)
        if self._cancelled or self.too_fast:
            return []
        return self.frames

    def _watchdog(self):
        if self._running:
            self._cancelled = True
            self._running = False
            self._teardown()
        return False


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
