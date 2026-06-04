"""Annotation objects. All coordinates are in the cropped image's pixel space.

Each annotation knows how to draw itself onto a cairo context. The `ctx` passed to
draw() exposes `blur_surface` (a pre-blurred copy of the base image) used by the
blur/pixelate tool.

Annotations also expose a small geometry protocol used by the editor's Select tool:
  bbox()             -> (x, y, w, h) bounding box
  set_bbox(x,y,w,h)  -> reshape to a new bounding box (box-style shapes)
  move(dx, dy)       -> translate
  contains(px,py,t)  -> is the point within tolerance t of the shape?
  handle_style       -> "box" (8 corner/edge handles) or "endpoints" (start/end)
  endpoints()/set_endpoint(name, x, y)  -> for line/arrow style shapes
"""
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import cairo
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .config import hex_to_rgba

_PANGO_ALIGN = {
    "left": Pango.Alignment.LEFT,
    "center": Pango.Alignment.CENTER,
    "right": Pango.Alignment.RIGHT,
    "justify": Pango.Alignment.LEFT,  # justify uses LEFT + set_justify(True)
}


def _set_color(cr, hex_color, alpha=1.0):
    cr.set_source_rgba(*hex_to_rgba(hex_color, alpha))


def _dist_to_segment(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


class Annotation:
    handle_style = "box"

    def draw(self, cr, ctx):
        raise NotImplementedError

    # --- geometry protocol (sensible defaults; overridden where needed) ---
    def bbox(self):
        return (0, 0, 0, 0)

    def set_bbox(self, x, y, w, h):
        pass

    def move(self, dx, dy):
        x, y, w, h = self.bbox()
        self.set_bbox(x + dx, y + dy, w, h)

    def contains(self, px, py, tol):
        x, y, w, h = self.bbox()
        return (x - tol <= px <= x + w + tol) and (y - tol <= py <= y + h + tol)


# ----------------------------------------------------------- endpoint shapes
@dataclass
class Arrow(Annotation):
    x0: float; y0: float; x1: float; y1: float
    color: str = "#ff3b30"
    width: float = 4
    handle_style = "endpoints"

    def draw(self, cr, ctx):
        _set_color(cr, self.color)
        cr.set_line_width(self.width)
        cr.set_line_cap(1)  # round
        cr.move_to(self.x0, self.y0)
        cr.line_to(self.x1, self.y1)
        cr.stroke()
        ang = math.atan2(self.y1 - self.y0, self.x1 - self.x0)
        head = max(12, self.width * 3.5)
        for da in (math.radians(28), -math.radians(28)):
            cr.move_to(self.x1, self.y1)
            cr.line_to(self.x1 - head * math.cos(ang - da),
                       self.y1 - head * math.sin(ang - da))
        cr.stroke()

    def bbox(self):
        x, y = min(self.x0, self.x1), min(self.y0, self.y1)
        return (x, y, abs(self.x1 - self.x0), abs(self.y1 - self.y0))

    def move(self, dx, dy):
        self.x0 += dx; self.y0 += dy; self.x1 += dx; self.y1 += dy

    def endpoints(self):
        return {"start": (self.x0, self.y0), "end": (self.x1, self.y1)}

    def set_endpoint(self, name, x, y):
        if name == "start":
            self.x0, self.y0 = x, y
        else:
            self.x1, self.y1 = x, y

    def contains(self, px, py, tol):
        return _dist_to_segment(px, py, self.x0, self.y0, self.x1, self.y1) \
            <= tol + self.width / 2


@dataclass
class Line(Arrow):
    """Same geometry as Arrow, without the arrowhead."""
    def draw(self, cr, ctx):
        _set_color(cr, self.color)
        cr.set_line_width(self.width)
        cr.set_line_cap(1)
        cr.move_to(self.x0, self.y0)
        cr.line_to(self.x1, self.y1)
        cr.stroke()


# ---------------------------------------------------------------- box shapes
@dataclass
class Rect(Annotation):
    x: float; y: float; w: float; h: float
    color: str = "#ff3b30"
    width: float = 4

    def draw(self, cr, ctx):
        _set_color(cr, self.color)
        cr.set_line_width(self.width)
        cr.set_line_join(1)
        cr.rectangle(self.x, self.y, self.w, self.h)
        cr.stroke()

    def bbox(self):
        return (self.x, self.y, self.w, self.h)

    def set_bbox(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    def contains(self, px, py, tol):
        # outline shape: hit only near the border, so you can draw inside it
        t = tol + self.width / 2
        outer = (self.x - t <= px <= self.x + self.w + t
                 and self.y - t <= py <= self.y + self.h + t)
        inner = (self.x + t < px < self.x + self.w - t
                 and self.y + t < py < self.y + self.h - t)
        return outer and not inner


@dataclass
class Ellipse(Rect):
    def draw(self, cr, ctx):
        _set_color(cr, self.color)
        cr.set_line_width(self.width)
        cr.save()
        cr.translate(self.x + self.w / 2, self.y + self.h / 2)
        if self.w and self.h:
            cr.scale(self.w / 2, self.h / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.stroke()

    def contains(self, px, py, tol):
        # hit near the ellipse curve only (hollow interior is click-through)
        rx, ry = (self.w / 2) or 1, (self.h / 2) or 1
        cx, cy = self.x + rx, self.y + ry
        r = math.hypot((px - cx) / rx, (py - cy) / ry)
        band = (tol + self.width / 2) / max(1, min(rx, ry))
        return abs(r - 1) <= band


@dataclass
class Blur(Annotation):
    x: float; y: float; w: float; h: float

    def draw(self, cr, ctx):
        surf = getattr(ctx, "blur_surface", None)
        if surf is None or self.w <= 0 or self.h <= 0:
            return
        cr.save()
        cr.rectangle(self.x, self.y, self.w, self.h)
        cr.clip()
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
        cr.restore()

    def bbox(self):
        return (self.x, self.y, self.w, self.h)

    def set_bbox(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


@dataclass
class Spotlight(Annotation):
    """One dark overlay with one or more focus rectangles ('holes') punched out.

    A single Spotlight holds every focus area, so adding more focus rectangles
    just punches more holes in the SAME dim layer — the darkness never stacks.
    The editor routes new spotlight drags into the existing Spotlight via
    add_hole(); the constructor still takes a first rect so a fresh drag works.
    """
    x: float; y: float; w: float; h: float
    darkness: float = 0.6
    holes: List[Tuple[float, float, float, float]] = None

    def rects(self):
        return [(self.x, self.y, self.w, self.h)] + list(self.holes or [])

    def add_hole(self, x, y, w, h):
        self.holes = list(self.holes or []) + [(x, y, w, h)]

    def draw(self, cr, ctx):
        W = getattr(ctx, "img_w", 0)
        H = getattr(ctx, "img_h", 0)
        if not W or not H:
            return
        cr.save()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.set_source_rgba(0, 0, 0, max(0.0, min(0.95, self.darkness)))
        cr.rectangle(0, 0, W, H)               # whole image
        for rx, ry, rw, rh in self.rects():    # punch out each focus area
            cr.rectangle(rx, ry, rw, rh)
        cr.fill()
        cr.restore()

    def bbox(self):
        rs = self.rects()
        x0 = min(r[0] for r in rs); y0 = min(r[1] for r in rs)
        x1 = max(r[0] + r[2] for r in rs); y1 = max(r[1] + r[3] for r in rs)
        return (x0, y0, x1 - x0, y1 - y0)

    def set_bbox(self, x, y, w, h):
        # Single hole: set directly. Multiple holes: scale them all to the new
        # union rectangle so the whole spotlight resizes/moves as a unit.
        rs = self.rects()
        if len(rs) == 1:
            self.x, self.y, self.w, self.h = x, y, w, h
            return
        ox, oy, ow, oh = self.bbox()
        sx = w / ow if ow else 1
        sy = h / oh if oh else 1
        scaled = [(x + (rx - ox) * sx, y + (ry - oy) * sy, rw * sx, rh * sy)
                  for rx, ry, rw, rh in rs]
        (self.x, self.y, self.w, self.h), self.holes = scaled[0], scaled[1:]

    def move(self, dx, dy):
        self.x += dx; self.y += dy
        if self.holes:
            self.holes = [(rx + dx, ry + dy, rw, rh)
                          for rx, ry, rw, rh in self.holes]

    def contains(self, px, py, tol):
        # grab by ANY focus-rectangle border (interiors stay click-through so you
        # can keep annotating inside a lit area)
        t = tol + 3
        for rx, ry, rw, rh in self.rects():
            outer = (rx - t <= px <= rx + rw + t and ry - t <= py <= ry + rh + t)
            inner = (rx + t < px < rx + rw - t and ry + t < py < ry + rh - t)
            if outer and not inner:
                return True
        return False


# -------------------------------------------------------------- stroke shapes
@dataclass
class Pen(Annotation):
    points: List[Tuple[float, float]] = field(default_factory=list)
    color: str = "#ff3b30"
    width: float = 4

    def draw(self, cr, ctx):
        if len(self.points) < 2:
            return
        _set_color(cr, self.color)
        cr.set_line_width(self.width)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.move_to(*self.points[0])
        for p in self.points[1:]:
            cr.line_to(*p)
        cr.stroke()

    def bbox(self):
        if not self.points:
            return (0, 0, 0, 0)
        xs = [p[0] for p in self.points]; ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def set_bbox(self, x, y, w, h):
        ox, oy, ow, oh = self.bbox()
        sx = w / ow if ow else 1
        sy = h / oh if oh else 1
        self.points = [(x + (px - ox) * sx, y + (py - oy) * sy)
                       for px, py in self.points]

    def move(self, dx, dy):
        self.points = [(px + dx, py + dy) for px, py in self.points]

    def contains(self, px, py, tol):
        t = tol + self.width / 2
        for a, b in zip(self.points, self.points[1:]):
            if _dist_to_segment(px, py, a[0], a[1], b[0], b[1]) <= t:
                return True
        return False


@dataclass
class Highlight(Pen):
    color: str = "#ffea00"
    width: float = 22

    def draw(self, cr, ctx):
        if len(self.points) < 2:
            return
        cr.push_group()
        _set_color(cr, self.color, 1.0)
        cr.set_line_width(self.width)
        cr.set_line_cap(2)  # square
        cr.set_line_join(1)
        cr.move_to(*self.points[0])
        for p in self.points[1:]:
            cr.line_to(*p)
        cr.stroke()
        cr.pop_group_to_source()
        cr.paint_with_alpha(0.4)


# --------------------------------------------------------------- text/counter
@dataclass
class Text(Annotation):
    """A text box rendered with Pango.

    - Font size is set explicitly (never by resizing the box).
    - `box_w` is the wrap width: None = auto (grows with content, floored at a
      minimum); a number = manual width (text wraps, height grows to fit).
    - `align` is one of left / center / right / justify.
    """
    x: float; y: float; text: str = ""
    color: str = "#ff3b30"
    size: float = 28
    align: str = "left"
    box_w: Optional[float] = None
    _w: float = 0.0   # last rendered text width  (cache for bbox / handles)
    _h: float = 0.0   # last rendered text height (cache for bbox / handles)

    PAD = 12.0        # breathing room between the text and the box frame

    def min_width(self):
        return max(48.0, self.size * 2.2)

    def build_layout(self, cr):
        """Configure a Pango layout for the current text/size/align/width and
        update the cached rendered size. Returns the layout."""
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family("Sans")
        fd.set_weight(Pango.Weight.BOLD)
        fd.set_absolute_size(self.size * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(self.text or "", -1)
        # natural (unwrapped) width first
        layout.set_width(-1)
        natural = layout.get_pixel_extents()[1].width
        eff = self.box_w if self.box_w else max(natural, self.min_width())
        layout.set_width(int(eff * Pango.SCALE))
        layout.set_alignment(_PANGO_ALIGN.get(self.align, Pango.Alignment.LEFT))
        layout.set_justify(self.align == "justify")
        if self.box_w:
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        log = layout.get_pixel_extents()[1]
        self._w = eff
        self._h = log.height
        return layout

    def draw(self, cr, ctx):
        layout = self.build_layout(cr)
        _set_color(cr, self.color)
        cr.move_to(self.x + self.PAD, self.y + self.PAD)  # inset from the frame
        PangoCairo.show_layout(cr, layout)

    def bbox(self):
        # The box is the text plus PAD on every side, so the frame never hugs
        # the glyphs.
        w = (self._w or (self.box_w or self.min_width())) + 2 * self.PAD
        h = (self._h or (self.size * 1.4)) + 2 * self.PAD
        return (self.x, self.y, w, h)

    def set_bbox(self, x, y, w, h):
        # Resizing sets the wrap WIDTH only — never the font size; height is
        # content-driven. The frame width includes padding, so subtract it.
        self.x, self.y = x, y
        self.box_w = max(self.min_width(), w - 2 * self.PAD)

    def move(self, dx, dy):
        self.x += dx; self.y += dy


@dataclass
class Counter(Annotation):
    x: float; y: float; number: int = 1
    color: str = "#ff3b30"
    radius: float = 18

    def draw(self, cr, ctx):
        _set_color(cr, self.color)
        cr.arc(self.x, self.y, self.radius, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("sans-serif", 0, 1)
        cr.set_font_size(self.radius * 1.2)
        label = str(self.number)
        ext = cr.text_extents(label)
        cr.move_to(self.x - ext.width / 2 - ext.x_bearing,
                   self.y - ext.height / 2 - ext.y_bearing)
        cr.show_text(label)

    def bbox(self):
        return (self.x - self.radius, self.y - self.radius,
                2 * self.radius, 2 * self.radius)

    def set_bbox(self, x, y, w, h):
        self.radius = max(6, min(w, h) / 2)
        self.x = x + w / 2
        self.y = y + h / 2

    def move(self, dx, dy):
        self.x += dx; self.y += dy

    def contains(self, px, py, tol):
        return math.hypot(px - self.x, py - self.y) <= self.radius + tol
