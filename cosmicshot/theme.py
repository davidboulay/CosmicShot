"""Light/dark detection and the shared visual language of CosmicShot's windows.

One palette + one stylesheet drive both the editor and the Settings window, so
they read as the same app. Every rule is scoped to ``.cs-*`` classes and only
touches our own widgets. The look follows Apple's interface conventions: quiet
chrome, rounded grouped controls, one accent colour reserved for the primary
action and the current state, and instant press feedback.
"""
import os
import subprocess
from pathlib import Path

ACCENT = "#0a84ff"         # system blue; replaced by the desktop accent if set

DARK = {
    "window": "#1c1c1e",
    "toolbar": "#232326",
    "group": "rgba(255,255,255,0.06)",
    "group_border": "rgba(255,255,255,0.07)",
    "sep": "rgba(255,255,255,0.10)",
    "tool_fg": "#e5e5ea",
    "tool_hover": "rgba(255,255,255,0.10)",
    "tool_press": "rgba(255,255,255,0.16)",
    "label": "#d1d1d6",
    "secondary": "rgba(235,235,245,0.55)",
    "card": "#2c2c2e",
    "card_border": "rgba(255,255,255,0.06)",
    "field": "rgba(255,255,255,0.08)",
    "float": "rgba(44,44,46,0.92)",
    "float_border": "rgba(255,255,255,0.10)",
    "canvas": (0.110, 0.110, 0.118),
    "icon": (0.90, 0.90, 0.92, 1.0),
    "shadow": 0.55,
}
LIGHT = {
    "window": "#f2f2f7",
    "toolbar": "#f6f6f8",
    "group": "rgba(0,0,0,0.05)",
    "group_border": "rgba(0,0,0,0.04)",
    "sep": "rgba(60,60,67,0.16)",
    "tool_fg": "#1c1c1e",
    "tool_hover": "rgba(0,0,0,0.07)",
    "tool_press": "rgba(0,0,0,0.12)",
    "label": "#3a3a3c",
    "secondary": "rgba(60,60,67,0.60)",
    "card": "#ffffff",
    "card_border": "rgba(0,0,0,0.06)",
    "field": "rgba(118,118,128,0.12)",
    "float": "rgba(255,255,255,0.94)",
    "float_border": "rgba(0,0,0,0.08)",
    "canvas": (0.906, 0.906, 0.922),
    "icon": (0.11, 0.11, 0.12, 1.0),
    "shadow": 0.22,
}


def _kde_globals():
    p = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeglobals"
    try:
        return p.read_text(errors="replace").splitlines()
    except OSError:
        return []


def _kde_value(lines, section, key):
    cur = None
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("[") and ln.endswith("]"):
            cur = ln[1:-1]
        elif cur == section and ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


def _rgb_triplet(v):
    try:
        r, g, b = (int(x) for x in v.split(",")[:3])
        return r, g, b
    except Exception:
        return None


def is_dark():
    """True if the desktop is in dark mode (COSMIC, KDE Plasma or GNOME-style)."""
    # 1) COSMIC theme mode
    p = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
         / "cosmic/com.system76.CosmicTheme.Mode/v1/is_dark")
    try:
        v = p.read_text().strip().lower()
        if v in ("true", "false"):
            return v == "true"
    except Exception:
        pass
    # 2) KDE Plasma: the window background of the active colour scheme
    if "KDE" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper():
        rgb = _rgb_triplet(_kde_value(_kde_globals(), "Colors:Window",
                                      "BackgroundNormal") or "")
        if rgb:
            r, g, b = rgb
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128
    # 3) freedesktop color-scheme (COSMIC and Plasma mirror this)
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2).stdout.strip().strip("'")
        if out and out != "default":
            return "dark" in out
    except Exception:
        pass
    # 4) GTK theme name
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        name = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
        return "dark" in name.lower()
    except Exception:
        pass
    return True


def accent():
    """The desktop accent colour as '#rrggbb' (KDE Plasma), else system blue."""
    lines = _kde_globals()
    for sec, key in (("General", "AccentColor"),
                     ("Colors:Selection", "BackgroundNormal")):
        rgb = _rgb_triplet(_kde_value(lines, sec, key) or "")
        if rgb and "KDE" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper():
            return "#%02x%02x%02x" % rgb
    return ACCENT


def palette(dark=None):
    if dark is None:
        dark = is_dark()
    return DARK if dark else LIGHT


def _mix(hexc, amount):
    """Lighten (amount>0) or darken (amount<0) a '#rrggbb' colour."""
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    t = 255 if amount > 0 else 0
    a = abs(amount)
    r, g, b = (round(c + (t - c) * a) for c in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)


def css(dark):
    """The shared stylesheet (bytes) for the given mode."""
    p = palette(dark)
    acc = accent()
    acc_hover = _mix(acc, 0.12)
    acc_press = _mix(acc, -0.12)
    ring = "#ffffff" if dark else "#1c1c1e"
    return f"""
/* ---------- shared ---------- */
.cs-window, .cs-window > .cs-root {{ background-color: {p['window']}; }}
.cs-window headerbar {{
  background-image: none; background-color: {p['toolbar']};
  border-bottom: 1px solid {p['sep']}; box-shadow: none;
  min-height: 44px; padding: 0 8px;
}}
.cs-window headerbar .title {{ font-weight: 600; color: {p['tool_fg']}; }}
.cs-window headerbar .subtitle {{ color: {p['secondary']}; font-feature-settings: "tnum"; }}

button.cs-flat {{
  background-image: none; background-color: transparent; border: none;
  box-shadow: none; color: {p['tool_fg']}; border-radius: 8px;
  min-height: 28px; min-width: 28px; padding: 2px 8px;
  transition: background-color 120ms ease-out;
}}
button.cs-flat:hover {{ background-color: {p['tool_hover']}; }}
button.cs-flat:active, button.cs-flat:checked {{ background-color: {p['tool_press']}; }}
button.cs-flat:disabled {{ opacity: 0.4; }}
button.cs-icon {{ padding: 2px 4px; }}

button.cs-primary, button.cs-secondary {{
  background-image: none; border: none; box-shadow: none;
  border-radius: 8px; min-height: 28px; padding: 2px 14px; font-weight: 600;
  transition: background-color 120ms ease-out;
}}
button.cs-primary {{ background-color: {acc}; color: #ffffff; }}
button.cs-primary:hover {{ background-color: {acc_hover}; }}
button.cs-primary:active {{ background-color: {acc_press}; }}
button.cs-primary label {{ color: #ffffff; }}
button.cs-secondary {{ background-color: {p['field']}; color: {p['tool_fg']}; font-weight: 500; }}
button.cs-secondary:hover {{ background-color: {p['tool_hover']}; }}
button.cs-secondary:active {{ background-color: {p['tool_press']}; }}
button.cs-secondary:disabled {{ opacity: 0.45; }}

/* press feedback: the glyph gives a little, instantly, on pointer-down */
button.cs-tool image, button.cs-flat image {{ transition: -gtk-icon-transform 90ms ease-out; }}
button.cs-tool:active image, button.cs-flat:active image {{ -gtk-icon-transform: scale(0.86); }}

/* ---------- editor ---------- */
.cs-toolbar {{
  background-color: {p['toolbar']};
  border-bottom: 1px solid {p['sep']};
  padding: 6px 10px;
}}
.cs-group {{
  background-color: {p['group']}; border: 1px solid {p['group_border']};
  border-radius: 10px; padding: 2px;
}}
.cs-divider {{ background-color: {p['sep']}; min-width: 1px; margin: 6px 3px; }}
button.cs-tool {{
  background-image: none; background-color: transparent;
  color: {p['tool_fg']}; border: none; box-shadow: none;
  border-radius: 8px; min-width: 32px; min-height: 30px;
  margin: 0; padding: 0 3px;
  transition: background-color 120ms ease-out;
}}
button.cs-tool:hover {{ background-color: {p['tool_hover']}; }}
button.cs-tool:checked {{ background-color: {acc}; color: #ffffff; }}
button.cs-tool:checked:hover {{ background-color: {acc_hover}; }}
button.cs-swatch {{
  background-image: none; border: 2px solid transparent; box-shadow: none;
  border-radius: 999px; min-width: 18px; min-height: 18px; padding: 0; margin: 4px 2px;
  box-shadow: inset 0 0 0 1px rgba(0,0,0,0.14);
  transition: border-color 120ms ease-out;
}}
button.cs-swatch:hover {{ border-color: {p['sep']}; }}
button.cs-swatch.selected {{ border-color: {ring}; }}
button.cs-colorwell {{
  background-image: none; background-color: transparent; border: none; box-shadow: none;
  padding: 2px; margin: 0 2px; border-radius: 999px; min-width: 0; min-height: 0;
}}
button.cs-colorwell:hover {{ background-color: {p['tool_hover']}; }}
button.cs-chip {{
  background-image: none; background-color: transparent; border: none; box-shadow: none;
  color: {p['label']}; border-radius: 8px; min-height: 30px; padding: 0 10px;
  transition: background-color 120ms ease-out, color 120ms ease-out;
}}
button.cs-chip:hover {{ background-color: {p['tool_hover']}; }}
button.cs-chip:checked {{ background-color: alpha({acc}, 0.20); color: {acc_hover}; }}
button.cs-chip:checked label {{ color: {acc_hover}; }}
checkbutton.cs-check {{
  padding: 0 10px 0 6px; margin-left: 2px; border-radius: 8px; min-height: 30px;
  transition: background-color 120ms ease-out;
}}
checkbutton.cs-check:hover {{ background-color: {p['tool_hover']}; }}
checkbutton.cs-check label {{ margin-left: 6px; }}
checkbutton.cs-check check {{
  -gtk-icon-source: none; background-image: none; background-color: {p['field']};
  border: 1px solid {p['group_border']}; border-radius: 5px;
  min-width: 16px; min-height: 16px; box-shadow: none;
  transition: background-color 120ms ease-out, border-color 120ms ease-out;
}}
checkbutton.cs-check check:checked {{
  background-color: {acc}; border-color: {acc};
  -gtk-icon-source: -gtk-icontheme("object-select-symbolic"); color: #ffffff;
}}
.cs-slider {{ margin: 0 2px; }}
.cs-toolbar label, .cs-pop label {{ color: {p['label']}; }}
.cs-value {{ font-feature-settings: "tnum"; min-width: 24px; color: {p['secondary']}; }}
/* the scale's own box is its hit area: pad it so the whole knob (and a bit
   around it) takes clicks, not just the 6px track */
.cs-toolbar scale, .cs-pop scale {{ padding: 11px 10px; }}
.cs-toolbar scale trough, .cs-pop scale trough {{
  background-image: none; background-color: {p['field']}; border: none;
  border-radius: 999px; min-height: 6px;
}}
.cs-toolbar scale highlight, .cs-pop scale highlight {{
  background-image: none; background-color: {acc}; border: none; border-radius: 999px;
}}
.cs-toolbar scale slider, .cs-pop scale slider {{
  background-image: none; background-color: #ffffff; border: none;
  border-radius: 999px; min-width: 20px; min-height: 20px; margin: -8px 0;
  box-shadow: 0 0 0 0.5px rgba(0,0,0,0.12), 0 1px 3px rgba(0,0,0,0.30);
  transition: -gtk-icon-transform 90ms ease-out;
}}
.cs-pop {{ padding: 10px; }}
popover.cs-popover {{
  background-color: {p['card']}; border: 1px solid {p['float_border']};
  border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,{p['shadow']});
}}
.cs-pop button.cs-row {{
  background-image: none; background-color: transparent; border: none; box-shadow: none;
  border-radius: 6px; padding: 4px 10px; min-height: 26px; color: {p['tool_fg']};
}}
.cs-pop button.cs-row:hover {{ background-color: {p['tool_hover']}; }}
.cs-pop button.cs-row:checked {{ background-color: {acc}; }}
.cs-pop button.cs-row:checked label {{ color: #ffffff; }}
.cs-caption {{ color: {p['secondary']}; font-size: 0.85em; font-weight: 600; }}
.cs-toolbar spinbutton, .cs-pop spinbutton {{
  border-radius: 8px; min-height: 28px; background-color: {p['field']};
  border: none; box-shadow: none;
}}

/* floating capsules over the canvas (zoom, crop) */
.cs-float {{
  background-color: {p['float']}; border: 1px solid {p['float_border']};
  border-radius: 12px; padding: 3px;
  box-shadow: 0 4px 16px rgba(0,0,0,{p['shadow']});
}}
.cs-float label {{ color: {p['label']}; font-feature-settings: "tnum"; }}

/* ---------- settings ---------- */
.cs-settings {{ padding: 20px 24px 28px 24px; }}
.cs-hero-name {{ font-size: 1.55em; font-weight: 700; letter-spacing: -0.01em; }}
.cs-hero-sub {{ color: {p['secondary']}; }}
.cs-section-title {{
  color: {p['secondary']}; font-size: 0.85em; font-weight: 600;
  letter-spacing: 0.02em; margin: 0 12px 6px 12px;
}}
.cs-footer {{ color: {p['secondary']}; font-size: 0.88em; margin: 6px 12px 0 12px; }}
.cs-card {{
  background-color: {p['card']}; border: 1px solid {p['card_border']};
  border-radius: 12px;
}}
.cs-card-row {{ padding: 9px 14px; min-height: 30px; }}
.cs-card-sep {{ background-color: {p['sep']}; min-height: 1px; margin-left: 14px; }}
.cs-keycap {{
  background-color: {p['field']}; border-radius: 6px; padding: 2px 8px;
  font-feature-settings: "tnum"; color: {p['label']};
}}
.cs-keycap.empty {{ background-color: transparent; color: {p['secondary']}; }}
.cs-card entry, .cs-card combobox button {{
  border-radius: 8px; background-image: none; background-color: {p['field']};
  border: none; box-shadow: none; min-height: 28px;
}}
.cs-card switch {{
  border-radius: 999px; border: none; background-image: none;
  background-color: {p['field']}; min-width: 40px; min-height: 22px;
}}
.cs-card switch:checked {{ background-color: #34c759; }}
.cs-card switch slider {{
  border-radius: 999px; background-image: none; background-color: #ffffff; border: none;
  min-width: 20px; min-height: 20px; margin: 1px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}}
""".encode()


_provider = None


def install(dark):
    """(Re)install the shared stylesheet for light/dark and tell GTK which mode
    we're in so the stock widgets and titlebar buttons match."""
    global _provider
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gtk, Gdk
        Gtk.Settings.get_default().set_property(
            "gtk-application-prefer-dark-theme", bool(dark))
        screen = Gdk.Screen.get_default()
        if _provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, _provider)
        _provider = Gtk.CssProvider()
        _provider.load_from_data(css(dark))
        Gtk.StyleContext.add_provider_for_screen(
            screen, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except Exception as exc:  # a CSS typo must never take the app down
        import sys
        print(f"cosmicshot: theme not applied: {exc}", file=sys.stderr)
