"""Upload a screenshot to a free image host and return its URL.

Configurable and host-agnostic. Works with:
  * plain-text URL responses (catbox.moe, 0x0.st, ...)
  * pomf-style JSON responses (uguu.se and clones: {"files":[{"url": ...}]})
  * a few other common JSON shapes (tmpfiles.org, generic {"url": ...})

Config keys (see config.py):
  upload_service : endpoint URL
  upload_field   : multipart file field name (e.g. "fileToUpload", "files[]", "file")
  upload_extra   : dict of extra form fields (e.g. {"reqtype": "fileupload"})
  upload_expires : optional retention hint (host-dependent)
"""
import requests

USER_AGENT = "CosmicShot/0.1 (screenshot tool)"


def _extract_url(resp):
    text = (resp.text or "").strip()
    if text.startswith("http") and "\n" not in text.strip():
        return text
    # try JSON shapes
    try:
        j = resp.json()
    except ValueError:
        if text.startswith("http"):
            return text.splitlines()[0].strip()
        raise RuntimeError(text[:200] or "empty response")
    if isinstance(j, dict):
        if isinstance(j.get("url"), str):
            return j["url"]
        files = j.get("files")
        if isinstance(files, list) and files and isinstance(files[0], dict):
            if files[0].get("url"):
                return files[0]["url"]
        data = j.get("data")
        if isinstance(data, dict) and data.get("url"):
            return data["url"]
    raise RuntimeError(f"Could not find a URL in response: {str(j)[:200]}")


def upload_image(png_bytes, cfg=None):
    cfg = cfg or {}
    url = cfg.get("upload_service") or "https://catbox.moe/user/api.php"
    field = cfg.get("upload_field") or "fileToUpload"
    extra = dict(cfg.get("upload_extra") or {})
    expires = cfg.get("upload_expires")
    if expires:
        extra.setdefault("expires", str(expires))
    files = {field: ("cosmicshot.png", png_bytes, "image/png")}
    resp = requests.post(url, files=files, data=extra,
                         headers={"User-Agent": USER_AGENT}, timeout=45)
    resp.raise_for_status()
    return _extract_url(resp)
