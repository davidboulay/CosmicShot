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


def _grab_while_importing():
    """Run the (subprocess) screenshot grab on a thread so the GTK/layer-shell
    import happens concurrently, shaving startup latency off every capture."""
    import threading
    monitors = capture.list_monitors()
    box = {}
    th = threading.Thread(target=lambda: box.__setitem__("p", capture.full()))
    th.start()
    from . import overlay  # heavy GTK import overlaps the grab
    th.join()
    return monitors, box["p"], overlay


def mode_region(cfg):
    monitors, shot_path, overlay = _grab_while_importing()
    rect = overlay.SelectionOverlay(shot_path, monitors).run()
    if not rect:
        return  # cancelled
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
    """Capture a whole screen. With multiple monitors (or always, per the
    picker), show a screen-wide overlay and let the user click the screen to
    capture; then open the editor with that monitor."""
    monitors, shot_path, overlay = _grab_while_importing()
    m = overlay.ScreenPicker(shot_path, monitors).run()
    if m is None:
        return  # cancelled
    img = Image.open(shot_path).convert("RGBA")
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
                   choices=["region", "full", "screen", "window", "open", "tray"],
                   help="region (default): drag-select then edit; "
                        "screen/full: pick a whole screen; window: COSMIC picker; "
                        "open: edit an existing image (give --file); "
                        "tray: run the panel tray icon.")
    p.add_argument("--file", help="image path for 'open' mode")
    args = p.parse_args(argv)

    _set_branding()
    cfg = config.load()

    # 'tray' is the background app and must not take the capture lock.
    if args.mode == "tray":
        from . import tray
        return tray.run_tray(cfg) or 0

    # One capture/editor session at a time: if an editor is already open,
    # don't let another capture pile up on top of it.
    from .lock import SingleInstance
    capture_lock = SingleInstance("capture")
    if not capture_lock.acquire():
        export.notify("CosmicShot", "A capture is already in progress.")
        return 0

    try:
        if args.mode == "region":
            mode_region(cfg)
        elif args.mode in ("full", "screen"):
            mode_full(cfg)
        elif args.mode == "window":
            mode_window(cfg)
        elif args.mode == "open":
            if not args.file:
                p.error("open mode requires --file PATH")
            mode_open(cfg, args.file)
    except FileNotFoundError as e:
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # surface failures to the user
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        capture_lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
