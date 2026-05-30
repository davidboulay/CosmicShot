#!/usr/bin/env bash
# Install CosmicShot for the current user (no root needed).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"
APPS="$SHARE/applications"
DEST="$SHARE/cosmicshot"

echo "Installing CosmicShot..."

# --- dependency check ---
missing=()
command -v cosmic-screenshot >/dev/null 2>&1 || missing+=("cosmic-screenshot")
command -v wl-copy >/dev/null 2>&1 || missing+=("wl-clipboard (wl-copy)")
python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('GtkLayerShell','0.1')" 2>/dev/null \
    || missing+=("python3-gi + gir1.2-gtklayershell-0.1")
python3 -c "import cairo" 2>/dev/null || missing+=("python3-gi-cairo / python3-cairo")
python3 -c "import PIL" 2>/dev/null || missing+=("python3-pil")
if [ ${#missing[@]} -ne 0 ]; then
    echo "WARNING: missing dependencies:"; printf '  - %s\n' "${missing[@]}"
    echo "On Pop!_OS/Ubuntu try:"
    echo "  sudo apt install python3-gi python3-gi-cairo python3-pil gir1.2-gtklayershell-0.1 wl-clipboard"
    echo
fi

# --- copy package ---
mkdir -p "$DEST" "$BIN" "$APPS"
rm -rf "$DEST/cosmicshot"
cp -r "$SRC/cosmicshot" "$DEST/cosmicshot"

# --- launcher ---
cat > "$BIN/cosmicshot" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$DEST\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m cosmicshot "\$@"
EOF
chmod +x "$BIN/cosmicshot"

# --- icon (themed, multiple sizes) ---
ICONS="$SHARE/icons/hicolor"
if [ -f "$SRC/data/cosmicshot.png" ]; then
    for sz in 16 24 32 48 64 128 256 512; do
        d="$ICONS/${sz}x${sz}/apps"; mkdir -p "$d"
        python3 - "$SRC/data/cosmicshot.png" "$d/cosmicshot.png" "$sz" <<'PY' 2>/dev/null \
            || cp "$SRC/data/cosmicshot.png" "$d/cosmicshot.png"
import sys
from PIL import Image
src, dst, sz = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.open(src).convert("RGBA").resize((sz, sz), Image.LANCZOS).save(dst)
PY
    done
    gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
fi

# --- desktop entry ---
cp "$SRC/data/cosmicshot.desktop" "$APPS/cosmicshot.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

# --- autostart the panel tray icon at login ---
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$AUTOSTART"
cat > "$AUTOSTART/cosmicshot-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=CosmicShot Tray
Comment=CosmicShot panel icon with a capture menu
Exec=$BIN/cosmicshot tray
Icon=cosmicshot
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

echo "Installed:"
echo "  launcher : $BIN/cosmicshot"
echo "  package  : $DEST/cosmicshot"
echo "  desktop  : $APPS/cosmicshot.desktop"
echo
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "NOTE: $BIN is not on your PATH. Add to ~/.bashrc:"
       echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo
echo "Tray icon: autostarts at login (remove $AUTOSTART/cosmicshot-tray.desktop to disable)."
echo "Try it:   cosmicshot region"
echo "Then bind a key in COSMIC Settings -> Keyboard -> Custom Shortcuts:"
echo "      cosmicshot region    (recommended: Super+Shift+S or PrtSc)"
