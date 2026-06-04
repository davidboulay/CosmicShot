"""Scrolling-screenshot stitcher (pure PIL, no numpy).

Manual-scroll model: the user scrolls a region while we grab frames of it; this
module detects the vertical overlap between consecutive frames and stacks the
newly-revealed strip onto a tall image. Works for downward scrolling.

``detect_overlap`` returns both the shift and a confidence so the capture UI can
warn when the user scrolled too fast (consecutive frames no longer overlap, so
the result can't be stitched reliably).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PIL import Image

_SCALE_W = 160        # downscale width used only for fast overlap matching
_BAND_H = 64          # height of the match band taken from the bottom of a frame
_MATCH_ERR = 16.0     # mean abs-diff below this = a confident content match
_CHANGE_ERR = 3.0     # frame-to-frame diff above this = the view actually moved


def _gray(img: Image.Image):
    g = img.resize((_SCALE_W, img.height)).convert("L")
    return g.tobytes(), _SCALE_W, img.height


def _band_err(ad, bd, w, atop, btop, band_h) -> float:
    err = 0
    for r in range(band_h):
        ao = (atop + r) * w
        bo = (btop + r) * w
        for c in range(w):
            d = ad[ao + c] - bd[bo + c]
            err += d if d >= 0 else -d
    return err / (band_h * w)


def detect_overlap(a: Image.Image, b: Image.Image) -> Tuple[int, float]:
    """Return (shift, err): how far content moved UP from a to b, and the match
    error at that shift (lower = better; large = no real overlap)."""
    ad, w, h = _gray(a)
    bd, _, _ = _gray(b)
    band_h = min(_BAND_H, h // 2)
    if band_h <= 0:
        return 0, 999.0
    atop = h - band_h
    best_s, best_err = 0, None
    for s in range(1, h - band_h):
        btop = atop - s
        if btop < 0:
            break
        e = _band_err(ad, bd, w, atop, btop, band_h)
        if best_err is None or e < best_err:
            best_err, best_s = e, s
    return best_s, (best_err if best_err is not None else 999.0)


def changed(a: Image.Image, b: Image.Image) -> bool:
    """Did the view move at all between two frames (cheap full-frame diff)?"""
    ad, w, h = _gray(a)
    bd, _, _ = _gray(b)
    step = max(1, (w * h) // 4000)  # sample for speed
    tot = cnt = 0
    for i in range(0, w * h, step):
        d = ad[i] - bd[i]
        tot += d if d >= 0 else -d
        cnt += 1
    return (tot / cnt) > _CHANGE_ERR if cnt else False


def is_confident(err: float) -> bool:
    return err <= _MATCH_ERR


def stitch(frames: List[Image.Image], min_step: int = 4) -> Optional[Image.Image]:
    """Stitch frames captured while scrolling down into one tall image."""
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0].convert("RGB")
    canvas = frames[0].convert("RGB")
    w = canvas.width
    for prev, cur in zip(frames, frames[1:]):
        if cur.width != w:
            cur = cur.resize((w, cur.height))
        s, err = detect_overlap(prev, cur)
        if s < min_step or not is_confident(err):
            continue
        new_strip = cur.crop((0, cur.height - s, w, cur.height)).convert("RGB")
        merged = Image.new("RGB", (w, canvas.height + s))
        merged.paste(canvas, (0, 0))
        merged.paste(new_strip, (0, canvas.height))
        canvas = merged
    return canvas
