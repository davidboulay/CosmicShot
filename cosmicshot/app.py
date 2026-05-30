"""Orchestration + CLI entry point.

Flow (region mode, the default):
    capture whole desktop -> dim overlay selector -> crop -> annotation editor
    -> copy / save / pin.
"""
import argparse
import os
import sys

from PIL import Image

from . import config, capture, export


def _set_branding():
    """Set the Wayland app_id and default window icon so the dock/tray and
    notifications show the CosmicShot icon instead of a generic placeholder."""
    try:
        from gi.repository import GLib, Gtk
        GLib.set_prgname(config.APP_ID)         # Wayland app_id -> matches .desktop
        try:
            GLib.set_application_name(config.APP_NAME)
        except Exception:
            pass
        icon = config.icon_path()
        if icon:
            Gtk.Window.set_default_icon_from_file(icon)
        else:
            Gtk.Window.set_default_icon_name(config.APP_ID)
    except Exception:
        pass


def _run_editor(pil_image, cfg):
    from .editor import Editor
    ed = Editor(pil_image, cfg)
    surface = ed.run()
    if surface is not None:
        from . import pin
        pin.pin(surface)  # runs its own loop until closed


def mode_region(cfg):
    monitors = capture.list_monitors()
    shot_path = capture.full()
    from .overlay import SelectionOverlay
    # Always start with a fresh crosshair (no pre-seeded last region — it got in
    # the way). The region is still recorded so `cosmicshot last` can reuse it.
    rect = SelectionOverlay(shot_path, monitors).run()
    if not rect:
        return  # cancelled
    cfg["last_region"] = list(rect)
    config.save(cfg)
    _edit_region(shot_path, rect, cfg)


def mode_last(cfg):
    """Re-capture the last-used region immediately, skipping the overlay."""
    rect = cfg.get("last_region")
    if not rect:
        return mode_region(cfg)  # nothing remembered yet
    shot_path = capture.full()
    full = Image.open(shot_path)
    x, y, w, h = rect
    # guard against a monitor-layout change
    if x < 0 or y < 0 or x + w > full.width or y + h > full.height:
        return mode_region(cfg)
    _edit_region(shot_path, rect, cfg)


def _edit_region(shot_path, rect, cfg):
    x, y, w, h = rect
    img = Image.open(shot_path).convert("RGBA").crop((x, y, x + w, y + h))
    if cfg.get("auto_copy_on_capture"):
        from .imaging import pil_to_surface
        surf, _buf = pil_to_surface(img)
        export.copy_to_clipboard(surf)
    _run_editor(img, cfg)


def mode_full(cfg):
    """Capture the monitor the pointer is on (whole desktop if single-screen)."""
    monitors = capture.list_monitors()
    shot_path = capture.full()
    img = Image.open(shot_path).convert("RGBA")
    if len(monitors) > 1:
        m = capture.monitor_at_pointer(monitors)
        x0, y0, x1, y1 = m.bounds
        img = img.crop((max(0, x0), max(0, y0),
                        min(x1, img.width), min(y1, img.height)))
    _run_editor(img, cfg)


def mode_window(cfg):
    # Use COSMIC's native interactive picker (lets you choose a window/region),
    # then drop straight into the editor.
    shot_path = capture.portal_interactive()
    if not shot_path or not os.path.exists(shot_path):
        return
    img = Image.open(shot_path).convert("RGBA")
    _run_editor(img, cfg)


def mode_open(cfg, path):
    img = Image.open(path).convert("RGBA")
    _run_editor(img, cfg)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="cosmicshot",
        description="CleanShot-style screenshot capture + annotation for COSMIC/Wayland.")
    p.add_argument("mode", nargs="?", default="region",
                   choices=["region", "last", "full", "window", "open", "tray"],
                   help="region (default): drag-select then edit; "
                        "last: re-capture the last-used region; "
                        "full: monitor under pointer; window: COSMIC picker; "
                        "open: edit an existing image (give --file); "
                        "tray: run the panel tray icon.")
    p.add_argument("--file", help="image path for 'open' mode")
    args = p.parse_args(argv)

    _set_branding()
    cfg = config.load()
    try:
        if args.mode == "region":
            mode_region(cfg)
        elif args.mode == "last":
            mode_last(cfg)
        elif args.mode == "full":
            mode_full(cfg)
        elif args.mode == "window":
            mode_window(cfg)
        elif args.mode == "open":
            if not args.file:
                p.error("open mode requires --file PATH")
            mode_open(cfg, args.file)
        elif args.mode == "tray":
            from . import tray
            tray.run_tray(cfg)
    except FileNotFoundError as e:
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # surface failures to the user
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
