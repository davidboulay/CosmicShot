#!/usr/bin/env bash
# CosmicShot one-line installer: adds the signed APT repo, then installs.
#   curl -fsSL https://davidboulay.github.io/CosmicShot/install.sh | sudo bash
# Runs the manual steps from the README for you. Re-running is safe (idempotent).
set -euo pipefail

BASE="https://davidboulay.github.io/CosmicShot"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo "Adding the CosmicShot APT repository…"
$SUDO install -d -m 0755 /etc/apt/keyrings
$SUDO curl -fsSL "$BASE/cosmicshot-archive-keyring.gpg" -o /etc/apt/keyrings/cosmicshot.gpg
echo "deb [signed-by=/etc/apt/keyrings/cosmicshot.gpg] $BASE stable main" \
  | $SUDO tee /etc/apt/sources.list.d/cosmicshot.list >/dev/null

echo "Installing CosmicShot…"
$SUDO apt-get update
$SUDO apt-get install -y cosmicshot

echo
echo "✓ CosmicShot installed. Launch it from the app grid or run: cosmicshot"
echo "  Updates arrive with your system via: sudo apt upgrade"
