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
        import gi
        gi.require_version("Gtk", "3.0")        # else the unversioned import
        gi.require_version("Gdk", "3.0")        # grabs GTK4 and clashes
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
    Editor(pil_image, cfg).run()


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
    """Pick a specific app window: hover to highlight, click to capture that
    whole window. Uses COSMIC's toplevel-info protocol for window geometry; if
    that's unavailable (no build tools / non-COSMIC), fall back to COSMIC's
    native interactive picker."""
    from . import windows
    wins = windows.list_windows()
    if not wins:
        shot_path = capture.portal_interactive()  # fallback
        if not shot_path or not os.path.exists(shot_path):
            return
        _run_editor(Image.open(shot_path).convert("RGBA"), cfg)
        return

    monitors = capture.list_monitors()
    shot_path = capture.full()
    from .overlay import WindowPicker
    rect = WindowPicker(shot_path, monitors, wins).run()
    if not rect:
        return  # cancelled
    x, y, w, h = rect
    full = Image.open(shot_path).convert("RGBA")
    img = full.crop((max(0, x), max(0, y),
                     min(x + w, full.width), min(y + h, full.height)))
    _run_editor(img, cfg)


def _pick_rect(target):
    """Return a global (x,y,w,h) rect for a scroll/record target, or None."""
    monitors = capture.list_monitors()
    shot_path = capture.full()
    from . import overlay
    if target == "screen":
        m = overlay.ScreenPicker(shot_path, monitors).run()
        if m is None:
            return None
        x0, y0, x1, y1 = m.bounds
        return (x0, y0, x1 - x0, y1 - y0)
    if target == "window":
        from . import windows
        wins = windows.list_windows()
        if not wins:
            return None
        return overlay.WindowPicker(shot_path, monitors, wins).run()
    # region (default): drag-select
    rect = overlay.SelectionOverlay(shot_path, monitors).run()
    return rect


def mode_scroll(cfg, target="region"):
    """Scrolling screenshot: pick a target, then capture frames while the user
    scrolls and stitch them into one tall image."""
    rect = _pick_rect(target)
    if not rect:
        return  # cancelled
    from . import overlay, scroll
    monitors = capture.list_monitors()
    # Manual scroll for all modes. Hands-free auto-scroll needs input injection,
    # which COSMIC (libseat) ignores for user-created uinput devices unless a
    # udev rule grants the device to the seat — so it isn't reliable here.
    sc = overlay.ScrollCapture(rect, monitors, capture)
    frames = sc.run()
    if getattr(sc, "too_fast", False):
        export.notify("CosmicShot", "Scrolled too fast — please retry, scrolling slowly.")
        return
    if not frames:
        return  # cancelled / nothing captured
    stitched = scroll.stitch(frames)
    if stitched is None:
        return
    if cfg.get("auto_copy_on_capture"):
        from .imaging import pil_to_surface
        surf, _buf = pil_to_surface(stitched.convert("RGBA"))
        export.copy_to_clipboard(surf)
    _run_editor(stitched.convert("RGBA"), cfg)


def mode_record(cfg, target="region"):
    """Record video of a region / app window / whole screen to an mp4 via the
    ScreenCast portal. The portal natively picks the window or monitor; region
    additionally crops to the selected rectangle. After recording you preview
    the clip and choose Save or Discard."""
    region = None
    if target == "region":
        region = _pick_rect("region")
        if not region:
            return  # cancelled before recording
    save_dir = cfg.get("save_dir") or os.path.join(os.path.expanduser("~"), "Videos")
    monitors = capture.list_monitors()
    from .record import RecordingSession
    sess = RecordingSession(target, save_dir, region=region, monitors=monitors)
    saved = sess.run()
    if saved and os.path.exists(saved):
        export.notify("Recording saved", sess.error or saved, saved)
    elif sess.error:
        export.notify("CosmicShot — recording failed", sess.error)


def mode_open(cfg, path):
    img = Image.open(path).convert("RGBA")
    _run_editor(img, cfg)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    from . import __version__
    p = argparse.ArgumentParser(
        prog="cosmicshot",
        description="CleanShot-style screenshot capture + annotation for COSMIC/Wayland.")
    p.add_argument("--version", action="version", version=f"CosmicShot {__version__}")
    p.add_argument("mode", nargs="?", default="region",
                   choices=["region", "full", "screen", "window", "scroll",
                            "record", "open", "tray", "settings"],
                   help="region (default): drag-select then edit; "
                        "screen/full: pick a whole screen; window: pick an app "
                        "window; scroll: scrolling screenshot (--target); "
                        "record: record video (--target); "
                        "open: edit an existing image (give --file); "
                        "tray: run the panel tray icon.")
    p.add_argument("--target", default="region",
                   choices=["region", "screen", "window"],
                   help="what to capture in 'scroll'/'record' mode (default: region)")
    p.add_argument("--stop", action="store_true",
                   help="stop the recording in progress (e.g. bind a hotkey to "
                        "'cosmicshot record --stop' to stop a full-screen recording)")
    p.add_argument("--file", help="image path for 'open' mode")
    args = p.parse_args(argv)

    _set_branding()
    cfg = config.load()

    # Stop a recording in progress (works without a tray — bind a hotkey to it).
    if args.mode == "record" and args.stop:
        from . import lock
        import signal
        pid = lock.recording_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGUSR1)
            except OSError:
                pass
        else:
            export.notify("CosmicShot", "No recording is in progress.")
        return 0

    # 'tray' is the background app and must not take the capture lock.
    if args.mode == "tray":
        from . import tray
        return tray.run_tray(cfg) or 0

    # Settings is a normal window, not a capture — no lock needed.
    if args.mode == "settings":
        from .settings import SettingsWindow
        SettingsWindow(cfg).run()
        return 0

    # Launched from the panel menu? The COSMIC panel keeps its tray menu open
    # after a click, so dismiss it before grabbing or it lands in the shot.
    if os.environ.get("COSMICSHOT_FROM_TRAY") and args.mode in (
            "region", "full", "screen", "window", "scroll", "record"):
        try:
            from . import overlay
            overlay.dismiss_popups()
        except Exception:
            pass

    # One capture/editor session at a time: if an editor is already open,
    # don't let another capture pile up on top of it.
    from . import lock
    capture_lock = lock.SingleInstance("capture")
    if not capture_lock.acquire():
        export.notify("CosmicShot", "A capture is already in progress.")
        # Bring the existing editor to the front instead of starting a new one.
        pid = lock.read_active_pid()
        if pid:
            import signal
            try:
                os.kill(pid, signal.SIGUSR1)
            except (OSError, ProcessLookupError):
                pass
        return 0

    try:
        if args.mode == "region":
            mode_region(cfg)
        elif args.mode in ("full", "screen"):
            mode_full(cfg)
        elif args.mode == "window":
            mode_window(cfg)
        elif args.mode == "scroll":
            mode_scroll(cfg, args.target)
        elif args.mode == "record":
            mode_record(cfg, args.target)
        elif args.mode == "open":
            if not args.file:
                p.error("open mode requires --file PATH")
            mode_open(cfg, args.file)
    except FileNotFoundError as e:
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # surface failures to the user
        import traceback
        traceback.print_exc()
        export.notify("CosmicShot error", str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        capture_lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
