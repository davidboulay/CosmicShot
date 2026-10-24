#!/usr/bin/env bash
# Build a CosmicShot .rpm (Fedora, RHEL-family, openSUSE).
# Output: dist/cosmicshot-<version>-1.<dist>.noarch.rpm
#
# The version is read from cosmicshot/config.py (the single source of truth)
# and handed to the spec as %{pkgver}; the source tarball is staged from this
# checkout whatever the directory is called.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="$(grep -oP 'VERSION\s*=\s*"\K[^"]+' "$SRC/cosmicshot/config.py" | head -1)"
[ -n "$VER" ] || { echo "Could not read VERSION from config.py" >&2; exit 1; }

PKG="cosmicshot"
command -v rpmbuild >/dev/null 2>&1 \
    || { echo "rpmbuild is not installed (Fedora: sudo dnf install rpm-build; Debian: sudo apt install rpm)" >&2; exit 1; }
echo "Building $PKG $VER RPM ..."

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# Source tarball with a top-level dir named cosmicshot-<version>, as %setup expects.
tar -C "$SRC" -czf "$STAGE/SOURCES/$PKG-$VER.tar.gz" \
    --exclude=.git --exclude=dist --exclude='__pycache__' --exclude='*.pyc' \
    --transform "s,^\./,$PKG-$VER/," .

# --nodeps so a Debian/Ubuntu host (and the GitHub runner) can build the
# noarch package without Fedora's BuildRequires being installed locally.
rpmbuild --nodeps --define "_topdir $STAGE" --define "pkgver $VER" \
    -bb "$SRC/$PKG.spec" >"$STAGE/rpmbuild.log" 2>&1 \
    || { cat "$STAGE/rpmbuild.log" >&2; exit 1; }

mkdir -p "$SRC/dist"
find "$STAGE/RPMS" -name '*.rpm' -exec cp {} "$SRC/dist/" \;
echo "Built:"
ls -1 "$SRC/dist/$PKG-$VER"*.rpm
