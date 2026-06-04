"""Render annotations to a final image and hand it off (clipboard / file / pin)."""
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import cairo

from . import config


class _Ctx:
    """Carries the blur surface + image size to annotation.draw()."""
    def __init__(self, blur_surface, img_w=0, img_h=0):
        self.blur_surface = blur_surface
        self.img_w = img_w
        self.img_h = img_h


def render(base_surface, blur_surface, annotations):
    """Return a fresh ARGB32 cairo surface with all annotations baked in."""
    w = base_surface.get_width()
    h = base_surface.get_height()
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(out)
    cr.set_source_surface(base_surface, 0, 0)
    cr.paint()
    ctx = _Ctx(blur_surface, w, h)
    # One combined spotlight dim layer (overlapping spotlights must not stack),
    # over the image but under the other annotations — matching the editor.
    from .tools import Spotlight
    spots = [a for a in annotations if isinstance(a, Spotlight)]
    if spots:
        Spotlight.draw_combined(cr, ctx, spots)
    for ann in annotations:
        if isinstance(ann, Spotlight):
            continue
        cr.save()
        ann.draw(cr, ctx)
        cr.restore()
    out.flush()
    return out


def surface_to_png_bytes(surface):
    import io
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()


def copy_to_clipboard(surface):
    """Put a PNG on the Wayland clipboard via wl-copy."""
    data = surface_to_png_bytes(surface)
    proc = subprocess.Popen(
        ["wl-copy", "--type", "image/png"], stdin=subprocess.PIPE)
    proc.communicate(data)
    return proc.returncode == 0


def copy_text_to_clipboard(text):
    """Put plain text (e.g. an uploaded URL) on the Wayland clipboard."""
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode())
    return proc.returncode == 0


def save_to_disk(surface, cfg=None, path=None):
    cfg = cfg or config.load()
    if path is None:
        save_dir = Path(os.path.expanduser(cfg["save_dir"]))
        save_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime(cfg["filename_pattern"])
        path = save_dir / name
    path = Path(path)
    surface.write_to_png(str(path))
    return str(path)


def notify(summary, body="", path=None):
    # Use the screenshot as the notification image when available, else the app icon.
    icon = path if (path and os.path.exists(path)) else (config.icon_path() or "")
    try:
        args = ["notify-send", "-a", config.APP_NAME]
        if icon:
            args += ["-i", icon]
        args += [summary, body]
        subprocess.Popen(args)
    except FileNotFoundError:
        pass
