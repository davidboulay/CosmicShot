"""Write CosmicShot global shortcuts into COSMIC's custom-shortcuts config.

On Wayland only the compositor owns global hotkeys, so to make our shortcuts
work system-wide we add ``Spawn("cosmicshot …")`` entries to COSMIC's
``…Shortcuts/v1/custom`` RON file. We edit it as text — removing only our own
entries and appending the configured ones — so the user's other shortcuts are
left byte-for-byte intact (and we back the file up first). COSMIC usually picks
up the change live; a re-login guarantees it.
"""
import os
import re
from pathlib import Path

from . import config

_CUSTOM = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
           / "cosmic" / "com.system76.CosmicSettings.Shortcuts" / "v1" / "custom")

_MOD_MAP = {
    "super": "Super", "meta": "Super", "mod4": "Super", "win": "Super",
    "shift": "Shift",
    "ctrl": "Ctrl", "control": "Ctrl",
    "alt": "Alt", "mod1": "Alt",
}
# Match one top-level map entry: "    ( … ): <Action>,"
_ENTRY_RE = re.compile(r"    \(.*?\n    \): [^\n]*,", re.DOTALL)


def parse_accel(accel):
    """'Super+Shift+S' -> (['Super','Shift'], 's'), or (mods, None) if no key."""
    mods, key = [], None
    for part in (p for p in accel.split("+") if p.strip()):
        canon = _MOD_MAP.get(part.strip().lower())
        if canon:
            if canon not in mods:
                mods.append(canon)
        else:
            key = part.strip()
    if key and len(key) == 1 and key.isalpha():
        key = key.lower()
    return mods, key


def _entry(accel, command, label):
    mods, key = parse_accel(accel)
    if not key:
        return None
    mod_lines = "".join(f"            {m},\n" for m in mods)
    return (
        "    (\n"
        "        modifiers: [\n"
        f"{mod_lines}"
        "        ],\n"
        f'        key: "{key}",\n'
        f'        description: Some("CosmicShot: {label}"),\n'
        f'    ): Spawn("{command}"),'
    )


def apply(mapping):
    """Rewrite COSMIC's custom shortcuts so our Spawn entries match ``mapping``
    (action_id -> accel string; '' clears). Returns True if the file changed."""
    cmds = {a: (label, command) for a, label, command in config.SHORTCUT_ACTIONS}

    text = _CUSTOM.read_text() if _CUSTOM.exists() else "{\n}\n"
    open_i, close_i = text.find("{"), text.rfind("}")
    body = text[open_i + 1:close_i] if (open_i != -1 and close_i != -1) else ""

    # Keep every entry that isn't one of ours.
    kept = [m.group(0) for m in _ENTRY_RE.finditer(body)
            if not re.search(r'\): Spawn\("cosmicshot', m.group(0))]

    # Build our entries from the mapping.
    ours = []
    for action_id, accel in (mapping or {}).items():
        if not accel or action_id not in cmds:
            continue
        label, command = cmds[action_id]
        e = _entry(accel, command, label)
        if e:
            ours.append(e)

    entries = kept + ours
    new_body = ("\n" + "\n".join(entries) + "\n") if entries else "\n"
    result = "{" + new_body + "}\n"

    if result == text:
        return False
    _CUSTOM.parent.mkdir(parents=True, exist_ok=True)
    if _CUSTOM.exists():
        try:
            (_CUSTOM.with_suffix(".cosmicshot.bak")).write_text(text)
        except OSError:
            pass
    _CUSTOM.write_text(result)
    return True
