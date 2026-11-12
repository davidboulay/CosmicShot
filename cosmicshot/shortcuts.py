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


def format_accel(mods, key):
    """(['Super','Shift'], 's') -> 'Super+Shift+S' (letters shown upper-case)."""
    if len(key) == 1 and key.isalpha():
        key = key.upper()
    return "+".join(list(mods) + [key])


def _keymap():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk  # importing Gtk 3 opens the display
    Gtk.init_check(None)
    display = Gdk.Display.get_default()
    return Gdk, (Gdk.Keymap.get_for_display(display) if display else None)


def base_keyname(keycode, state, group):
    """Name of the key's unshifted symbol (e.g. '4' for the key that types '$'
    with Shift), or None. COSMIC matches shortcut keys against these base
    symbols, so a binding saved as Shift + "dollar" would never fire.
    ``state`` should carry only modifiers that pick the symbol, like NumLock."""
    try:
        Gdk, km = _keymap()
        if km is None:
            return None
        res = km.translate_keyboard_state(keycode, state, group)
        return Gdk.keyval_name(res[1]) if res[0] else None
    except Exception:
        return None


def _unshift(key):
    """Older versions saved the shifted symbol ('dollar'). Map it back to its
    base key on the current layout ('4'). Keys that are typed without Shift
    on some key of this layout are left alone."""
    if len(key) == 1 and key.isalpha():
        return key
    try:
        Gdk, km = _keymap()
        kv = Gdk.keyval_from_name(key)
        if km is None or kv in (0, Gdk.KEY_VoidSymbol):
            return key
        ok, keys = km.get_entries_for_keyval(kv)
        # Keycodes above 255 are evdev extras like KEY_DOLLAR that every
        # layout maps but real keyboards lack; only physical keys count.
        keys = [k for k in (keys if ok else []) if k.keycode <= 255]
        if not keys or any(k.level == 0 for k in keys):
            return key
        k = keys[0]
        return base_keyname(k.keycode, Gdk.ModifierType(0), k.group) or key
    except Exception:
        return key


def migrate(cfg):
    """Fix shortcuts recorded by <= 1.8.0 as Shift + shifted symbol (issue #7).
    Rewrites the config and COSMIC's file once; returns True if anything changed."""
    sc = dict(cfg.get("shortcuts") or {})
    changed = False
    for aid, accel in sc.items():
        if not accel:
            continue
        mods, key = parse_accel(accel)
        if not key or "Shift" not in mods:
            continue
        base = _unshift(key)
        if base != key:
            sc[aid] = format_accel(mods, base)
            changed = True
    if not changed:
        return False
    cfg["shortcuts"] = sc
    config.save(cfg)
    try:
        apply(sc)
    except Exception:
        pass
    return True


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
