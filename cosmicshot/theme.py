"""Detect COSMIC's light/dark preference and expose matching editor palettes."""
import os
import subprocess
from pathlib import Path

ACCENT = "#007aff"

DARK = {
    "toolbar": "#16171b",
    "group": "rgba(255,255,255,0.05)",
    "sep": "rgba(255,255,255,0.09)",
    "tool_fg": "#d3d4d9",
    "tool_hover": "rgba(255,255,255,0.13)",
    "label": "#c6c7cc",
    "canvas": (0.12, 0.12, 0.13),
    "icon": (0.85, 0.86, 0.90, 1.0),
}
LIGHT = {
    "toolbar": "#eef0f3",
    "group": "rgba(0,0,0,0.06)",
    "sep": "rgba(0,0,0,0.13)",
    "tool_fg": "#3a3b40",
    "tool_hover": "rgba(0,0,0,0.10)",
    "label": "#45464b",
    "canvas": (0.90, 0.90, 0.92),
    "icon": (0.20, 0.21, 0.25, 1.0),
}


def is_dark():
    """True if the desktop is in dark mode. Prefers COSMIC's own setting."""
    # 1) COSMIC theme mode
    p = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
         / "cosmic/com.system76.CosmicTheme.Mode/v1/is_dark")
    try:
        v = p.read_text().strip().lower()
        if v in ("true", "false"):
            return v == "true"
    except Exception:
        pass
    # 2) freedesktop color-scheme (COSMIC mirrors this)
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2).stdout.strip().strip("'")
        if out:
            return "dark" in out
    except Exception:
        pass
    # 3) GTK theme name
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        name = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
        return "dark" in name.lower()
    except Exception:
        pass
    return True


def palette(dark=None):
    if dark is None:
        dark = is_dark()
    return DARK if dark else LIGHT
