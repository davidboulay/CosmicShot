# CosmicShot RPM spec (Fedora / RHEL-family / openSUSE).
# Built by build-rpm.sh, which passes the version from cosmicshot/config.py:
#   rpmbuild --define "pkgver X.Y.Z" -ba cosmicshot.spec
%{!?pkgver: %global pkgver 0.0.0}

Name:           cosmicshot
Version:        %{pkgver}
Release:        1%{?dist}
Summary:        Screenshot and screen-recording tool for COSMIC/Wayland
BuildArch:      noarch

License:        MIT
URL:            https://github.com/davidboulay/CosmicShot
Source0:        cosmicshot-%{version}.tar.gz

BuildRequires:  python3-devel
BuildRequires:  python3-pillow
Requires:       python3
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk3
Requires:       gtk-layer-shell
Requires:       python3-pillow
Requires:       python3-requests
Requires:       wl-clipboard
# cosmic-screenshot ships with the COSMIC desktop itself (cosmic-session /
# cosmic-comp); it is not a separately packaged dependency.
Recommends:     libayatana-appindicator-gtk3
Recommends:     python3-gstreamer1
Recommends:     pipewire-gstreamer
Recommends:     gstreamer1-plugins-good
Recommends:     gstreamer1-vaapi
Recommends:     gstreamer1-plugins-bad-free
Suggests:       gstreamer1-libav
Suggests:       gcc
Suggests:       wayland-devel
Suggests:       wayland-protocols-devel

%description
CosmicShot is a fast screenshot and screen-recording tool for the COSMIC
desktop (Pop!_OS) and other Wayland compositors. Region / screen / window
capture, an annotation editor (arrows, text, shapes, blur, highlight, crop),
scrolling screenshots, MP4 screen recording (with optional audio), a pinned
floating preview, a panel tray icon, and a settings panel with one-click
updates and global shortcuts.

%prep
%setup -q -n cosmicshot-%{version}

%build
# Nothing to build: pure Python.

%install
mkdir -p %{buildroot}%{_datadir}/%{name}/%{name}
cp -r cosmicshot/* %{buildroot}%{_datadir}/%{name}/%{name}/
rm -rf %{buildroot}%{_datadir}/%{name}/%{name}/__pycache__

mkdir -p %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<'BINEOF'
#!/usr/bin/env bash
# /usr/bin/python3 on purpose: the dependencies are distro packages.
export PYTHONPATH="%{_datadir}/%{name}${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m cosmicshot "$@"
BINEOF
chmod 0755 %{buildroot}%{_bindir}/%{name}

mkdir -p %{buildroot}%{_datadir}/applications
cp data/cosmicshot.desktop %{buildroot}%{_datadir}/applications/cosmicshot.desktop

mkdir -p %{buildroot}%{_sysconfdir}/xdg/autostart
cat > %{buildroot}%{_sysconfdir}/xdg/autostart/cosmicshot-tray.desktop <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=CosmicShot Tray
Comment=CosmicShot panel icon with a capture menu
Exec=cosmicshot tray
Icon=cosmicshot
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
AUTOSTART

# Themed icons in several sizes (falls back to the raw PNG without Pillow).
for sz in 16 24 32 48 64 128 256 512; do
    d="%{buildroot}%{_datadir}/icons/hicolor/${sz}x${sz}/apps"
    mkdir -p "$d"
    python3 -c "import sys; from PIL import Image; Image.open(sys.argv[1]).convert('RGBA').resize((int(sys.argv[3]), int(sys.argv[3])), Image.LANCZOS).save(sys.argv[2])" data/cosmicshot.png "$d/cosmicshot.png" $sz 2>/dev/null || cp data/cosmicshot.png "$d/cosmicshot.png"
done

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/cosmicshot.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/cosmicshot-tray.desktop
%{_datadir}/icons/hicolor/*/apps/cosmicshot.png

%changelog
* Sat Oct 24 2026 David Boulay <david.boulay@lojel.com> - 1.6.0-1
- Version now comes from build-rpm.sh (cosmicshot/config.py); fixed Fedora
  package names; launcher pins /usr/bin/python3

* Wed Aug 26 2026 Sebs - 1.5.0-1
- Initial RPM package
