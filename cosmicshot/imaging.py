"""PIL <-> cairo conversion and the blur/pixelate source image."""
import cairo
from PIL import Image, ImageFilter


def pil_to_surface(im):
    """Convert a PIL image to a cairo ARGB32 surface.

    Returns (surface, buffer). The buffer MUST be kept alive for as long as the
    surface is used (cairo does not copy it).
    """
    im = im.convert("RGBA")
    w, h = im.size
    # cairo FORMAT_ARGB32 on little-endian == premultiplied BGRA bytes.
    buf = bytearray(im.tobytes("raw", "BGRa"))
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    surface = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, w, h, stride)
    return surface, buf


def make_pixelated(im, block=12):
    """Mosaic/pixelate the whole image (used as the blur tool's paint source)."""
    w, h = im.size
    block = max(2, int(block))
    small = im.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
    return small.resize((w, h), Image.NEAREST)


def make_blurred(im, radius=14):
    return im.convert("RGBA").filter(ImageFilter.GaussianBlur(radius))
