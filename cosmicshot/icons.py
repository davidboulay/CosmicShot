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
    """An open hand, drawn as one outline in the same line weight as the other
    tools (the old filled silhouette read as a blob beside the outline icons).
    Four rounded fingers of staggered height share their side lines, a thumb
    rests against the index finger and the palm curves down into the wrist —
    the Select tool grabs and moves shapes, matching its grab cursor."""
    cr.set_line_width(1.9)
    r = 1.8                                       # fingertip radius
    fingers = ((5.0, 7.2, 13.6), (8.6, 4.6, 11.0),  # (left x, tip y, base y)
               (12.2, 5.4, 11.0), (15.8, 8.0, 11.4))
    for x, top, base in fingers[:3]:
        cr.move_to(x, base)
        cr.line_to(x, top)
        cr.arc(x + r, top, r, math.pi, 0)
        cr.line_to(x + 2 * r, 11.0)
        cr.stroke()
    x, top, _ = fingers[3]                        # little finger -> palm -> wrist
    cr.move_to(x, 11.4)
    cr.line_to(x, top)
    cr.arc(x + r, top, r, math.pi, 0)
    cr.line_to(x + 2 * r, 14.0)
    cr.curve_to(x + 2 * r, 18.6, 16.4, 21.4, 12.2, 21.4)
    cr.line_to(11.0, 21.4)
    cr.curve_to(8.6, 21.4, 7.1, 20.6, 5.7, 19.2)
    cr.line_to(2.9, 16.3)                         # thumb, round tip, back in
    cr.arc(4.2, 15.0, 1.84, math.radians(135), math.radians(315))
    cr.line_to(6.6, 14.9)
    cr.stroke()


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
    """A pixelation mosaic: a 4x4 checkerboard of filled blocks.

    Every block is filled — the earlier version outlined the alternate cells,
    and at 18 px those 1.2-wide strokes muddied into a dark blob. Filled-only
    keeps the checkerboard legible and the weight in line with the outline icons.
    """
    n, g, s0, span = 4, 0.9, 4.2, 15.6
    cell = (span - (n - 1) * g) / n
    for i in range(n):
        for j in range(n):
            if (i + j) % 2:
                continue
            cr.rectangle(s0 + i * (cell + g), s0 + j * (cell + g), cell, cell)
    cr.fill()


def _spotlight(cr):
    """A torch (flashlight) throwing light: the Spotlight tool lights one area
    and darkens the rest. A short handle and a wide flared head on a 45° axis,
    with three rays fanning out of the lens."""
    cr.save()
    cr.translate(9.4, 14.6)
    cr.rotate(math.radians(-45))
    cr.set_line_width(1.9)
    _round_rect(cr, -7.6, -2.2, 6.0, 4.4, 1.4)        # handle
    cr.stroke()
    cr.move_to(-1.6, -2.2)                             # flared head
    cr.line_to(2.6, -4.8)
    cr.line_to(2.6, 4.8)
    cr.line_to(-1.6, 2.2)
    cr.close_path()
    cr.stroke()
    cr.move_to(5.4, 0); cr.line_to(9.4, 0)             # light rays
    cr.move_to(5.0, -4.4); cr.line_to(7.6, -7.0)
    cr.move_to(5.0, 4.4); cr.line_to(7.6, 7.0)
    cr.stroke()
    cr.restore()


def _crop(cr):
    cr.set_line_width(_LW)
    cr.move_to(8, 3.5); cr.line_to(8, 16); cr.line_to(20.5, 16)     # bracket 1
    cr.stroke()
    cr.move_to(3.5, 8); cr.line_to(16, 8); cr.line_to(16, 20.5)     # bracket 2
    cr.stroke()


# --- interface glyphs (header, zoom, alignment) — same grid and weight, so
#     every icon in the window comes from one hand instead of mixing whatever
#     the icon theme ships (Breeze/Papirus symbolic zoom icons rendered as
#     broken boxes in the editor). ---
def _undo(cr, mirror=False):
    cr.save()
    if mirror:
        cr.translate(24, 0); cr.scale(-1, 1)
    cr.set_line_width(2.0)
    cr.move_to(5.5, 9.5)
    cr.line_to(14.5, 9.5)
    cr.curve_to(17.8, 9.5, 19.5, 11.6, 19.5, 14.2)
    cr.curve_to(19.5, 16.8, 17.8, 18.8, 14.5, 18.8)
    cr.line_to(10.5, 18.8)
    cr.stroke()
    cr.move_to(9.2, 5.6); cr.line_to(5.3, 9.5); cr.line_to(9.2, 13.4)
    cr.stroke()
    cr.restore()


def _redo(cr):
    _undo(cr, mirror=True)


def _zoom_in(cr):
    cr.set_line_width(2.0)
    cr.move_to(6, 12); cr.line_to(18, 12)
    cr.move_to(12, 6); cr.line_to(12, 18)
    cr.stroke()


def _zoom_out(cr):
    cr.set_line_width(2.0)
    cr.move_to(6, 12); cr.line_to(18, 12)
    cr.stroke()


def _fit(cr):
    cr.set_line_width(2.0)
    for (x, y, dx, dy) in ((5, 5, 1, 1), (19, 5, -1, 1), (5, 19, 1, -1), (19, 19, -1, -1)):
        cr.move_to(x, y + dy * 4.5); cr.line_to(x, y); cr.line_to(x + dx * 4.5, y)
    cr.stroke()


def _align(cr, kind):
    cr.set_line_width(2.0)
    rows = ((6.5, 14), (10.5, 9), (14.5, 14), (18.5, 7))
    for y, w in rows:
        if kind == "justify":
            x0, x1 = 5, 19
        elif kind == "center":
            x0, x1 = 12 - w / 2, 12 + w / 2
        elif kind == "right":
            x0, x1 = 19 - w, 19
        else:
            x0, x1 = 5, 5 + w
        cr.move_to(x0, y); cr.line_to(x1, y)
    cr.stroke()


def _chevron(cr):
    cr.set_line_width(2.0)
    cr.move_to(7.5, 10); cr.line_to(12, 14.5); cr.line_to(16.5, 10)
    cr.stroke()


def _stroke(cr):
    """Line-weight glyph for the thickness control: three bars, thin to thick."""
    for y, w in ((7, 1.2), (12, 2.2), (17.5, 3.6)):
        cr.set_line_width(w)
        cr.move_to(5, y); cr.line_to(19, y)
        cr.stroke()


def _drop(cr):
    """Blur strength: a soft droplet."""
    cr.set_line_width(1.9)
    cr.move_to(12, 4.5)
    cr.curve_to(15.5, 9, 18, 11.8, 18, 14.6)
    cr.curve_to(18, 18, 15.3, 20, 12, 20)
    cr.curve_to(8.7, 20, 6, 18, 6, 14.6)
    cr.curve_to(6, 11.8, 8.5, 9, 12, 4.5)
    cr.close_path()
    cr.stroke()


def _moon(cr):
    """Spotlight darkness: a half-shaded disc (the usual contrast glyph)."""
    cr.set_line_width(1.9)
    cr.arc(12, 12, 7.6, 0, 2 * math.pi)
    cr.stroke()
    cr.arc(12, 12, 7.6, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()
    cr.fill()


_ICONS = {
    "select": _select, "arrow": _arrow, "rect": _rect, "ellipse": _ellipse,
    "line": _line, "pen": _pen, "highlight": _highlight, "text": _text,
    "counter": _counter, "blur": _blur, "spotlight": _spotlight, "crop": _crop,
    "undo": _undo, "redo": _redo, "zoom-in": _zoom_in, "zoom-out": _zoom_out,
    "fit": _fit, "chevron": _chevron, "stroke": _stroke, "drop": _drop,
    "moon": _moon,
    "align-left": lambda cr: _align(cr, "left"),
    "align-center": lambda cr: _align(cr, "center"),
    "align-right": lambda cr: _align(cr, "right"),
    "align-justify": lambda cr: _align(cr, "justify"),
}


def _paint(name, px, rgba):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    cr.scale(px / 24.0, px / 24.0)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(*rgba)
    fn = _ICONS.get(name)
    if fn:
        fn(cr)
    surf.flush()
    return surf


def pixbuf(name, size, rgba):
    """Return a GdkPixbuf of the named icon at `size` px in colour `rgba`
    (r, g, b, a floats 0..1)."""
    surf = _paint(name, size, rgba)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


def surface(name, size, rgba, scale=1):
    """A cairo surface of the icon at `size` logical px, rendered at the
    monitor's `scale` so it stays crisp on HiDPI (a 1x pixbuf is upscaled and
    goes soft at 200%). Use with Gtk.Image.new_from_surface / set_from_surface."""
    scale = max(1, int(scale or 1))
    surf = _paint(name, size * scale, rgba)
    surf.set_device_scale(scale, scale)
    return surf


def image(name, size, rgba, scale=1):
    """A ready Gtk.Image for the icon (HiDPI-aware)."""
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    return Gtk.Image.new_from_surface(surface(name, size, rgba, scale))
