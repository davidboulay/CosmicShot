"""Update checks against GitHub Releases + one-click .deb install via pkexec.

CosmicShot is shipped as a ``.deb`` on the repo's Releases page. We query the
GitHub API for the latest release, compare its tag to ``config.VERSION``, and —
if newer — can download the ``.deb`` and install it with ``pkexec apt-get``
(which prompts for the password). Network/JSON failures degrade silently.
"""
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.request

from . import config

_API = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
_TIMEOUT = 8


def _parse_version(tag):
    """'v1.2.3' / '1.2.3' -> (1, 2, 3) for comparison; non-numeric parts -> 0."""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest_tag, current=config.VERSION):
    return _parse_version(latest_tag) > _parse_version(current)


def check_latest():
    """Return a dict {version, tag, url, deb_url, notes} for the latest release,
    or None on any failure / no .deb asset."""
    try:
        req = urllib.request.Request(_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CosmicShot/{config.VERSION}",
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name") or ""
    deb_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".deb"):
            deb_url = asset.get("browser_download_url")
            break
    return {
        "tag": tag,
        "version": tag.lstrip("v"),
        "url": data.get("html_url"),
        "deb_url": deb_url,
        "notes": data.get("body") or "",
    }


def available():
    """Return the release info dict if a newer version is published, else None."""
    info = check_latest()
    if info and info["tag"] and is_newer(info["tag"]):
        return info
    return None


def download_deb(deb_url):
    """Download the .deb to a temp file; return its path or None."""
    if not deb_url:
        return None
    try:
        fd, path = tempfile.mkstemp(prefix="cosmicshot-", suffix=".deb")
        os.close(fd)
        req = urllib.request.Request(deb_url, headers={
            "User-Agent": f"CosmicShot/{config.VERSION}"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, open(path, "wb") as f:
            f.write(r.read())
        return path
    except Exception:
        return None


def install_deb(deb_path):
    """Install the .deb with pkexec (prompts for the password). Returns True on
    success. Runs in the foreground; call from a worker thread for the UI."""
    if not deb_path or not os.path.exists(deb_path):
        return False
    try:
        # apt-get handles dependencies; the absolute path makes it a local install.
        res = subprocess.run(
            ["pkexec", "apt-get", "install", "-y", "--reinstall", deb_path],
            capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False
