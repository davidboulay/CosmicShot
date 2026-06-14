"""Persistent configuration and shared defaults."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cosmicshot"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Branding. APP_ID is also the Wayland app_id / desktop file basename, so the
# COSMIC dock can match windows to the installed .desktop entry and icon.
APP_ID = "cosmicshot"
APP_NAME = "CosmicShot"
VERSION = "1.1.4"                       # single source of truth; matches the git tag
GITHUB_REPO = "davidboulay/CosmicShot"  # for the update check
ICON_FILE = str(Path(__file__).resolve().parent / "cosmicshot.png")  # bundled fallback
# Red ⏹ stop button shown in the panel while a recording is in progress.
STOP_ICON_NAME = "cosmicshot-stop"
STOP_ICON_FILE = str(Path(__file__).resolve().parent / "cosmicshot-stop.png")


def icon_path():
    """Prefer the installed themed icon, else the bundled one."""
    themed = (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
              / "icons/hicolor/512x512/apps" / f"{APP_ID}.png")
    if themed.exists():
        return str(themed)
    return ICON_FILE if Path(ICON_FILE).exists() else None

DEFAULTS = {
    # Where finished screenshots are saved when "Save" is pressed.
    "save_dir": str(Path.home() / "Pictures" / "Screenshots"),
    # Filename pattern; strftime tokens allowed.
    "filename_pattern": "CosmicShot_%Y-%m-%d_%H-%M-%S.png",
    # Default annotation appearance.
    "default_color": "#ff3b30",   # CleanShot-ish red
    "default_width": 4,
    "default_font_size": 28,
    # Palette shown in the toolbar.
    "palette": [
        "#ff3b30",  # red
        "#ff9500",  # orange
        "#ffcc00",  # yellow
        "#34c759",  # green
        "#007aff",  # blue
        "#af52de",  # purple
        "#000000",  # black
        "#ffffff",  # white
    ],
    # Pixelation block size used by the blur tool.
    "pixelate_block": 12,
    # Copy to clipboard automatically when the editor opens (CleanShot does on capture).
    "auto_copy_on_capture": False,
    # After Save, also copy to clipboard.
    "copy_on_save": True,
    # Drop shadow / margin added around exported image (CleanShot signature look). 0 to disable.
    "export_padding": 0,
    "export_bg": "#00000000",  # transparent padding background
    # Spotlight (focus) tool: how dark the surrounding area is (0..0.95).
    "spotlight_darkness": 0.6,
    # Cloud upload. Default: catbox.moe — free, no account, PERMANENT links.
    # Public host (anyone with the URL can view) — redact sensitive bits first.
    # Configurable for other hosts, e.g. open-source uguu.se (links expire ~3h):
    #   "upload_service": "https://uguu.se/upload.php",
    #   "upload_field": "files[]", "upload_extra": {}
    "upload_service": "https://catbox.moe/user/api.php",
    "upload_field": "fileToUpload",
    "upload_extra": {"reqtype": "fileupload"},
    "upload_expires": None,   # optional retention in hours (host-dependent)
    # Updates: check GitHub Releases on launch + periodically, notify, and offer
    # one-click install of the .deb (pkexec).
    "auto_update": False,
    # Global keyboard shortcuts written into COSMIC's custom-shortcuts config.
    # Maps an action id -> accelerator string like "Super+Shift+S" ("" = unset).
    # Empty by default; set them in Settings.
    "shortcuts": {},
}

# Actions that can be bound to a global shortcut (id -> (label, command)).
SHORTCUT_ACTIONS = [
    ("region", "Capture Region", "cosmicshot region"),
    ("screen", "Capture Screen", "cosmicshot screen"),
    ("window", "Capture App Window", "cosmicshot window"),
    ("scroll_region", "Scrolling Screenshot (Region)", "cosmicshot scroll --target region"),
    ("scroll_window", "Scrolling Screenshot (App Window)", "cosmicshot scroll --target window"),
]


def load():
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text()))
    except Exception:
        pass
    return cfg


def save(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


def hex_to_rgba(h, alpha=1.0):
    """'#rrggbb' or '#rrggbbaa' -> (r, g, b, a) floats 0..1."""
    h = h.lstrip("#")
    if len(h) == 8:
        r, g, b, a = (int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
        return r / 255, g / 255, b / 255, a / 255
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return r / 255, g / 255, b / 255, alpha
