#!/usr/bin/env bash
# Build a CosmicShot .deb (the package the in-app updater downloads & installs).
# Output: dist/cosmicshot_<version>_all.deb
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="$(grep -oP 'VERSION\s*=\s*"\K[^"]+' "$SRC/cosmicshot/config.py" | head -1)"
[ -n "$VER" ] || { echo "Could not read VERSION from config.py" >&2; exit 1; }

PKG="cosmicshot"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
echo "Building $PKG $VER ..."

# --- payload layout (mirrors the released package) ---
install -d "$STAGE/usr/share/cosmicshot/cosmicshot"
cp -r "$SRC/cosmicshot/." "$STAGE/usr/share/cosmicshot/cosmicshot/"
rm -rf "$STAGE/usr/share/cosmicshot/cosmicshot/__pycache__"

install -d "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/cosmicshot" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/usr/share/cosmicshot${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m cosmicshot "$@"
EOF
chmod 0755 "$STAGE/usr/bin/cosmicshot"

install -d "$STAGE/usr/share/applications"
cp "$SRC/data/cosmicshot.desktop" "$STAGE/usr/share/applications/cosmicshot.desktop"

install -d "$STAGE/etc/xdg/autostart"
cat > "$STAGE/etc/xdg/autostart/cosmicshot-tray.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=CosmicShot Tray
Comment=CosmicShot panel icon with a capture menu
Exec=cosmicshot tray
Icon=cosmicshot
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

# --- icons (themed, several sizes) ---
for sz in 16 24 32 48 64 128 256 512; do
    d="$STAGE/usr/share/icons/hicolor/${sz}x${sz}/apps"
    install -d "$d"
    python3 - "$SRC/data/cosmicshot.png" "$d/cosmicshot.png" "$sz" <<'PY' 2>/dev/null \
        || cp "$SRC/data/cosmicshot.png" "$d/cosmicshot.png"
import sys
from PIL import Image
src, dst, sz = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.open(src).convert("RGBA").resize((sz, sz), Image.LANCZOS).save(dst)
PY
done

# --- control metadata + maintainer scripts ---
install -d "$STAGE/DEBIAN"
SIZE_KB="$(du -ks "$STAGE/usr" "$STAGE/etc" | awk '{s+=$1} END {print s}')"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: cosmicshot
Version: $VER
Architecture: all
Maintainer: David Boulay <david.boulay@lojel.com>
Installed-Size: $SIZE_KB
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, gir1.2-gtklayershell-0.1, python3-pil, wl-clipboard
Recommends: gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1, python3-gst-1.0, gstreamer1.0-pipewire, gstreamer1.0-plugins-good, gstreamer1.0-vaapi, gstreamer1.0-plugins-bad, gstreamer1.0-libav
Suggests: gcc, libwayland-dev, wayland-protocols
Section: graphics
Priority: optional
Homepage: https://github.com/davidboulay/CosmicShot
Description: CleanShot-style screenshot, screen-recording & annotation for COSMIC/Wayland
 CosmicShot is a fast screenshot and screen-recording tool for the COSMIC
 desktop (Pop!_OS) and other Wayland compositors. Region / screen / window
 capture, an annotation editor (arrows, text, shapes, blur, highlight, crop),
 scrolling screenshots, MP4 screen recording (with optional audio), a pinned
 floating preview, a panel tray icon, and a settings panel with one-click
 updates and global shortcuts.
 .
 Requires the COSMIC screenshot backend (cosmic-screenshot). Recording needs
 PipeWire + GStreamer (pulled in as recommended packages).
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
EOF
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# --- build ---
mkdir -p "$SRC/dist"
OUT="$SRC/dist/${PKG}_${VER}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT" >/dev/null
echo "Built: $OUT"
