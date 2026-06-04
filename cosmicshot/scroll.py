"""Scrolling-screenshot stitcher (pure PIL, no numpy).

Manual-scroll model: the user scrolls a region while we grab frames of it; this
module detects the vertical overlap between consecutive frames and stacks the
newly-revealed strip onto a tall image. Works for downward scrolling.
"""
from __future__ import annotations

from typing import List, Optional

from PIL import Image

_SCALE_W = 120        # downscale width used only for fast overlap matching
_BAND_H = 50          # height of the match band taken from the bottom of a frame


def _gray_cols(img: Image.Image):
    g = img.resize((_SCALE_W, img.height)).convert("L")
    return g.tobytes(), _SCALE_W, img.height


def detect_shift(a: Image.Image, b: Image.Image, max_shift: Optional[int] = None) -> int:
    """Pixels the content moved UP from frame ``a`` to frame ``b`` (>=0).

    Matches a band from the bottom of ``a`` against ``b`` at each vertical
    offset and returns the best. 0 means no detectable downward scroll.
    """
    ad, w, h = _gray_cols(a)
    bd, _, _ = _gray_cols(b)
    band_h = min(_BAND_H, h // 2)
    if band_h <= 0:
        return 0
    if max_shift is None:
        max_shift = h - band_h
    atop = h - band_h
    best_s, best_err = 0, None
    for s in range(1, max_shift):
        btop = atop - s
        if btop < 0:
            break
        err = 0
        for r in range(band_h):
            ao = (atop + r) * w
            bo = (btop + r) * w
            err += sum(abs(ad[ao + c] - bd[bo + c]) for c in range(w))
        err /= band_h * w
        if best_err is None or err < best_err:
            best_err, best_s = err, s
    # Reject weak matches (no real scroll / unrelated frames).
    if best_err is None or best_err > 40:
        return 0
    return best_s


def stitch(frames: List[Image.Image], min_step: int = 4) -> Optional[Image.Image]:
    """Stitch frames captured while scrolling down into one tall image."""
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0]
    canvas = frames[0].convert("RGB")
    w = canvas.width
    for prev, cur in zip(frames, frames[1:]):
        if cur.width != w:
            cur = cur.resize((w, cur.height))
        s = detect_shift(prev, cur)
        if s < min_step:
            continue  # no meaningful new content
        new_strip = cur.crop((0, cur.height - s, w, cur.height)).convert("RGB")
        merged = Image.new("RGB", (w, canvas.height + s))
        merged.paste(canvas, (0, 0))
        merged.paste(new_strip, (0, canvas.height))
        canvas = merged
    return canvas
