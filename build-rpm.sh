#!/usr/bin/env bash
# Build a CosmicShot .rpm (for Fedora, RHEL, openSUSE)
# Output: dist/cosmicshot-<version>-1.fc44.noarch.rpm (or similar)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="$(grep -oP 'VERSION\s*=\s*"\K[^"]+' "$SRC/cosmicshot/config.py" | head -1)"
[ -n "$VER" ] || { echo "Could not read VERSION from config.py" >&2; exit 1; }

PKG="cosmicshot"
echo "Building $PKG $VER RPM..."

# Check for rpmbuild
if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "Error: rpmbuild is not installed. (sudo dnf install rpm-build)" >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Set up local rpmbuild tree
mkdir -p "$STAGE"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# Create source tarball
# The spec file expects the folder to be named 'CosmicShot'
(cd "$SRC/.." && tar -czf "$STAGE/SOURCES/$PKG-$VER.tar.gz" --exclude=".git" --exclude="dist" "CosmicShot")

# Build RPM
rpmbuild --nodeps --define "_topdir $STAGE" -ba "$SRC/cosmicshot.spec" >/dev/null

# Move output to dist/
mkdir -p "$SRC/dist"
find "$STAGE/RPMS" -name "*.rpm" -exec cp {} "$SRC/dist/" \;

echo "Built:"
ls -1 "$SRC/dist/"*.rpm
