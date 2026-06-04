"""Enumerate open windows (with geometry) on COSMIC.

COSMIC exposes per-window geometry only via its `zcosmic_toplevel_info_v1`
protocol, which has no Python bindings. So we ship a tiny C helper
(``winpick/helper.c`` + vendored protocol XML), compile it on first use with
the system ``wayland-scanner`` + ``gcc`` + ``libwayland`` dev files, cache the
binary, and parse its JSON. Any failure (missing build tools, protocol absent)
returns an empty list so the caller can fall back to the portal picker.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "winpick"
_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cosmicshot"

# (xml filename, needs a client-header?) — helper.c #includes the first two.
_PROTOCOLS = [
    ("cosmic-toplevel-info-unstable-v1.xml", True),
    ("ext-foreign-toplevel-list-v1.xml", True),
    ("ext-workspace-v1.xml", False),
    ("cosmic-workspace-unstable-v1.xml", False),
]


def _fingerprint() -> str:
    h = hashlib.sha256()
    for p in [_SRC / "helper.c"] + [_SRC / x for x, _ in _PROTOCOLS]:
        try:
            h.update(p.read_bytes())
        except OSError:
            return ""
    return h.hexdigest()[:16]


def _tools_present() -> bool:
    return all(shutil.which(t) for t in ("wayland-scanner", "gcc", "pkg-config"))


def _build() -> "Path | None":
    """Compile the helper into the cache dir. Returns the binary path or None."""
    if not _tools_present():
        return None
    fp = _fingerprint()
    if not fp:
        return None
    build = _CACHE / f"winpick-{fp}"
    binary = build / "cosmic-winlist"
    if binary.exists():
        return binary
    try:
        build.mkdir(parents=True, exist_ok=True)
        gen_c = []
        for xml, want_header in _PROTOCOLS:
            src = _SRC / xml
            stem = src.stem
            out_c = build / f"{stem}.c"
            subprocess.run(["wayland-scanner", "private-code", str(src), str(out_c)],
                           check=True, capture_output=True)
            gen_c.append(str(out_c))
            if want_header:
                subprocess.run(["wayland-scanner", "client-header", str(src),
                                str(build / f"{stem}-client.h")],
                               check=True, capture_output=True)
        cflags = subprocess.run(["pkg-config", "--cflags", "--libs", "wayland-client"],
                                capture_output=True, text=True, check=True).stdout.split()
        subprocess.run(
            ["gcc", "-O2", "-o", str(binary), str(_SRC / "helper.c"), *gen_c,
             f"-I{build}", *cflags],
            check=True, capture_output=True,
        )
        return binary if binary.exists() else None
    except (subprocess.SubprocessError, OSError):
        return None


def available() -> bool:
    return _build() is not None


def list_windows(timeout: float = 6.0):
    """Return visible windows as {x, y, w, h, app_id, title, active} in global
    pixel space, ordered bottom-to-top (the helper emits z-order; the topmost
    window is last). Minimized windows are dropped — they aren't on screen.
    Order is preserved so the picker can select the front-most window at a point.
    Empty list if unavailable."""
    binary = _build()
    if binary is None:
        return []
    try:
        out = subprocess.run([str(binary)], capture_output=True, text=True,
                             timeout=timeout).stdout
        wins = json.loads(out or "[]")
    except (subprocess.SubprocessError, OSError, ValueError):
        return []
    return [w for w in wins
            if w.get("w", 0) > 0 and w.get("h", 0) > 0 and not w.get("minimized")]
