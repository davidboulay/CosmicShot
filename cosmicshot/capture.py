"""Screen capture backend.

On COSMIC/Wayland we cannot freely read framebuffer pixels, so we delegate the
actual grab to `cosmic-screenshot` (which talks to the COSMIC screenshot portal).

Two strategies:
  * full(): silent grab of the entire desktop (all outputs) to a temp PNG. We then
    show our own overlay selector / editor on top of it -- the CleanShot feel.
  * portal_interactive(): use COSMIC's own interactive selection UI (fallback).

The full-desktop PNG is saved by cosmic-screenshot at device (physical)
resolution, while GTK reports monitor geometry in logical pixels. At a
fractional display scale the two differ — e.g. 150% on a 2560x1600 panel
gives a 2560x1600 PNG but a ~1707x1067 logical desktop — so logical
rectangles never go into the PNG directly: every crop goes through ImageMap.
"""
import glob
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402


@dataclass
class Monitor:
    index: int
    x: int
    y: int
    width: int
    height: int
    scale: int
    primary: bool
    model: str

    @property
    def bounds(self):
        return (self.x, self.y, self.x + self.width, self.y + self.height)


def list_monitors():
    display = Gdk.Display.get_default()
    mons = []
    for i in range(display.get_n_monitors()):
        m = display.get_monitor(i)
        g = m.get_geometry()
        mons.append(Monitor(
            index=i, x=g.x, y=g.y, width=g.width, height=g.height,
            scale=m.get_scale_factor(), primary=m.is_primary(),
            model=m.get_model() or f"monitor-{i}",
        ))
    return mons


def monitor_at_pointer(monitors=None):
    """Return the Monitor the mouse pointer is currently on (or primary)."""
    monitors = monitors or list_monitors()
    display = Gdk.Display.get_default()
    px = py = None
    try:
        ptr = display.get_default_seat().get_pointer()
        _scr, px, py = ptr.get_position()
    except Exception:
        pass
    if px is not None:
        for m in monitors:
            if m.x <= px < m.x + m.width and m.y <= py < m.y + m.height:
                return m
    return next((m for m in monitors if m.primary), monitors[0])


def desktop_bounds(monitors=None):
    """Union rectangle of all monitors, in logical pixels."""
    monitors = monitors or list_monitors()
    x0 = min(m.x for m in monitors)
    y0 = min(m.y for m in monitors)
    x1 = max(m.x + m.width for m in monitors)
    y1 = max(m.y + m.height for m in monitors)
    return (x0, y0, x1, y1)


class ImageMap:
    """Converts logical desktop rects to full-desktop PNG pixels.

    The ratio is derived from the actual PNG size (not from GTK's scale
    factor, which is integer-rounded — it reports 2 at a 150% scale), so it
    self-calibrates to whatever cosmic-screenshot actually saves. Uniform
    scaling is assumed: exact for one monitor or identical monitor scales;
    mixed-DPI layouts are approximated."""

    def __init__(self, image_width, image_height, monitors=None):
        x0, y0, x1, y1 = desktop_bounds(monitors)
        dw, dh = x1 - x0, y1 - y0
        self.rx = image_width / dw if dw > 0 else 1.0
        self.ry = image_height / dh if dh > 0 else 1.0

    def rect(self, x, y, w, h):
        """Logical (x, y, w, h) -> integer image-pixel (x, y, w, h)."""
        return (round(x * self.rx), round(y * self.ry),
                round(w * self.rx), round(h * self.ry))


def _newest_png(directory, since):
    files = [f for f in glob.glob(os.path.join(directory, "*.png"))
             if os.path.getmtime(f) >= since - 1]
    if not files:
        files = glob.glob(os.path.join(directory, "*.png"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def full(timeout=15):
    """Silently capture the whole desktop. Returns path to a PNG in a temp dir."""
    tmp = tempfile.mkdtemp(prefix="cosmicshot-")
    start = time.time()
    proc = subprocess.run(
        ["cosmic-screenshot", "--interactive=false", "--notify=false",
         "--save-dir", tmp],
        capture_output=True, text=True, timeout=timeout,
    )
    # cosmic-screenshot prints the path on stdout.
    out = (proc.stdout or "").strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.endswith(".png") and os.path.exists(line):
            return line
    path = _newest_png(tmp, start)
    if not path:
        raise RuntimeError(
            "cosmic-screenshot produced no file. stderr:\n" + (proc.stderr or ""))
    return path


def portal_interactive(timeout=120):
    """Use COSMIC's native interactive selection. Returns PNG path or None if cancelled."""
    tmp = tempfile.mkdtemp(prefix="cosmicshot-")
    start = time.time()
    proc = subprocess.run(
        ["cosmic-screenshot", "--interactive=true", "--notify=false",
         "--save-dir", tmp],
        capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "").strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.endswith(".png") and os.path.exists(line):
            return line
    return _newest_png(tmp, start)
