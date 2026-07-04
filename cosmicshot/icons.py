"""Crisp monochrome line-art icons for the editor toolbar.

Each icon is drawn on a 24x24 grid in a single colour (passed in), so the same
set works on light and dark themes — regenerate with the theme's foreground
colour when the theme changes. ``pixbuf(name, size, rgba)`` returns a GdkPixbuf.
"""
import math

import cairo
import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

_LW = 2.1  # base stroke width in the 24-unit space


def _round_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _arrow_shape(cr, x0, y0, x1, y1, lw, hl, hw):
    ang = math.atan2(y1 - y0, x1 - x0)
    L = math.hypot(x1 - x0, y1 - y0)
    cr.set_line_width(lw)
    cr.move_to(x0, y0)
    cr.line_to(x0 + math.cos(ang) * max(0, L - hl * 0.85),
               y0 + math.sin(ang) * max(0, L - hl * 0.85))
    cr.stroke()
    bx, by = x1 - math.cos(ang) * hl, y1 - math.sin(ang) * hl
    px, py = math.cos(ang + math.pi / 2), math.sin(ang + math.pi / 2)
    cr.move_to(x1, y1)
    cr.line_to(bx + px * hw, by + py * hw)
    cr.line_to(bx - px * hw, by - py * hw)
    cr.close_path()
    cr.fill()


def _select(cr):
    for x, y in [(5, 3), (5, 18.5), (9, 14.4), (11.6, 20.5),
                 (13.9, 19.4), (11.2, 13.6), (16, 13.6)]:
        cr.line_to(x, y)
    cr.close_path()
    cr.fill()


def _arrow(cr):
    _arrow_shape(cr, 5, 19, 18.5, 5.5, _LW, 7.0, 3.6)


def _rect(cr):
    cr.set_line_width(_LW)
    _round_rect(cr, 4, 6.5, 16, 11, 2.5)
    cr.stroke()


def _ellipse(cr):
    cr.set_line_width(_LW)
    cr.save()
    cr.translate(12, 12)
    cr.scale(8, 6.2)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.stroke()


def _line(cr):
    cr.set_line_width(_LW)
    cr.move_to(4.5, 19.5)
    cr.line_to(19.5, 4.5)
    cr.stroke()


def _pen(cr):
    cr.save()
    cr.translate(12, 12)
    cr.rotate(math.radians(45))
    cr.set_line_width(_LW)
    _round_rect(cr, -7.5, -2.4, 11.5, 4.8, 1.4)   # barrel
    cr.stroke()
    cr.move_to(4, -2.4); cr.line_to(7.6, 0); cr.line_to(4, 2.4)  # nib
    cr.close_path(); cr.fill()
    cr.move_to(-4.2, -2.4); cr.line_to(-4.2, 2.4); cr.stroke()   # eraser band
    cr.restore()


def _highlight(cr):
    cr.save()
    cr.translate(12, 12)
    cr.rotate(math.radians(45))
    cr.set_line_width(_LW)
    _round_rect(cr, -7.5, -3.5, 10, 7, 1.6)       # wide marker body
    cr.stroke()
    cr.move_to(2.5, -3.5); cr.line_to(7.5, -2.2)  # chisel tip (flat)
    cr.line_to(7.5, 2.2); cr.line_to(2.5, 3.5)
    cr.close_path(); cr.fill()
    cr.restore()


def _text(cr):
    cr.set_line_width(2.4)
    cr.move_to(5.5, 6.5); cr.line_to(18.5, 6.5)   # top bar
    cr.move_to(12, 6.5); cr.line_to(12, 19)        # stem
    cr.stroke()


def _counter(cr):
    cr.set_line_width(_LW)
    cr.arc(12, 12, 8, 0, 2 * math.pi)
    cr.stroke()
    cr.set_line_width(2.0)
    cr.move_to(10.5, 10); cr.line_to(12.2, 8.6); cr.line_to(12.2, 16)  # a "1"
    cr.stroke()


def _blur(cr):
    n, g, s0 = 3, 0.8, 5.0
    cell = (14 - (n - 1) * g) / n
    for i in range(n):
        for j in range(n):
            x = s0 + i * (cell + g)
            y = s0 + j * (cell + g)
            cr.rectangle(x, y, cell, cell)
            if (i + j) % 2 == 0:
                cr.fill()
            else:
                cr.set_line_width(1.2)
                cr.stroke()


def _spotlight(cr):
    cr.set_line_width(_LW)
    cr.arc(12, 12, 8, 0, 2 * math.pi); cr.stroke()
    cr.set_line_width(1.6)
    cr.arc(12, 12, 4.6, 0, 2 * math.pi); cr.stroke()
    cr.arc(12, 12, 1.7, 0, 2 * math.pi); cr.fill()


def _crop(cr):
    cr.set_line_width(_LW)
    cr.move_to(8, 3.5); cr.line_to(8, 16); cr.line_to(20.5, 16)     # bracket 1
    cr.stroke()
    cr.move_to(3.5, 8); cr.line_to(16, 8); cr.line_to(16, 20.5)     # bracket 2
    cr.stroke()


_ICONS = {
    "select": _select, "arrow": _arrow, "rect": _rect, "ellipse": _ellipse,
    "line": _line, "pen": _pen, "highlight": _highlight, "text": _text,
    "counter": _counter, "blur": _blur, "spotlight": _spotlight, "crop": _crop,
}


def pixbuf(name, size, rgba):
    """Return a GdkPixbuf of the named tool icon at `size` px in colour `rgba`
    (r, g, b, a floats 0..1)."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.scale(size / 24.0, size / 24.0)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(*rgba)
    fn = _ICONS.get(name)
    if fn:
        fn(cr)
    surf.flush()
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)
