"""Pin a screenshot to the screen: a borderless, always-on-top floating window.

Uses gtk-layer-shell (TOP layer) so it stays above normal windows on Wayland.
Drag to move, scroll to resize, double-click or Esc to close.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell  # noqa: E402
import cairo  # noqa: E402


class PinWindow(Gtk.Window):
    def __init__(self, surface, scale=0.5):
        super().__init__()
        self.surface = surface
        self.img_w = surface.get_width()
        self.img_h = surface.get_height()
        self.scale = scale
        self.margin = [80, 80]  # left, top

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, self.margin[0])
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self.margin[1])
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.KEY_PRESS_MASK)
        self.area.connect("draw", self.on_draw)
        self.add(self.area)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("scroll-event", self.on_scroll)
        self.connect("key-press-event", self.on_key)

        self._drag = None
        self._resize()

    def _resize(self):
        w = max(40, int(self.img_w * self.scale))
        h = max(40, int(self.img_h * self.scale))
        self.area.set_size_request(w, h)
        self.resize(w, h)

    def on_draw(self, _w, cr):
        a = self.area.get_allocation()
        sx = a.width / self.img_w
        sy = a.height / self.img_h
        # subtle shadow border
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(0, 0, a.width, a.height)
        cr.fill()
        cr.save()
        cr.scale(sx, sy)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, a.width - 1, a.height - 1)
        cr.stroke()
        return False

    def on_press(self, _w, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            self.destroy()
            return True
        if ev.button == 1:
            self._drag = (ev.x_root, ev.y_root, self.margin[0], self.margin[1])
        return True

    def on_motion(self, _w, ev):
        if self._drag:
            sx, sy, m0, m1 = self._drag
            nl = int(m0 + (ev.x_root - sx))
            nt = int(m1 + (ev.y_root - sy))
            self.margin = [max(0, nl), max(0, nt)]
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, self.margin[0])
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self.margin[1])
        return True

    def on_scroll(self, _w, ev):
        step = 0.08
        if ev.direction == Gdk.ScrollDirection.UP:
            self.scale = min(3.0, self.scale + step)
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            self.scale = max(0.1, self.scale - step)
        self._resize()
        return True

    def on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.destroy()
        return True


def pin(surface, run_main=True):
    win = PinWindow(surface)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    if run_main:
        Gtk.main()
