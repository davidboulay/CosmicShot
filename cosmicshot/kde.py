"""Register CosmicShot global shortcuts with KDE Plasma (kglobalaccel).

Plasma's own "Add New › Command" shortcut is a hidden ``.desktop`` launcher
plus a binding on its ``_launch`` action in kglobalaccel. We do the same over
D-Bus, so a shortcut recorded in CosmicShot works right away on Plasma.

Shortcuts are stored in CosmicShot's config the COSMIC way ("Ctrl+Shift+4":
modifiers + unshifted key). KWin matches differently: for a non-letter key it
drops Shift and uses the shifted symbol, so Ctrl+Shift+4 arrives as "Ctrl+$"
(kwin src/xkb.cpp, modifiersRelevantForGlobalShortcuts). We translate both
ways with the live keyboard layout. Only used when running under Plasma.
"""
import os
import shutil
from pathlib import Path

from . import config, shortcuts

_BUS = "org.kde.kglobalaccel"
_PATH = "/kglobalaccel"
_IFACE = "org.kde.KGlobalAccel"
_ACTION = "_launch"
_MARKER = "X-CosmicShot-Shortcut=true"
_SET_FLAGS = 0x2 | 0x4  # SetPresent (grab the key now) | NoAutoloading (set exactly these keys)

_APPS = (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
         / "applications")

# Qt modifier bits and named keys (Qt::Key values) for non-printable keys.
_QT_MODS = [("Super", 0x10000000), ("Ctrl", 0x04000000),
            ("Alt", 0x08000000), ("Shift", 0x02000000)]
_QT_MOD_MASK = 0xFE000000
_QT_NAMED = {
    "Escape": 0x01000000, "Tab": 0x01000001, "ISO_Left_Tab": 0x01000002,
    "BackSpace": 0x01000003, "Return": 0x01000004, "KP_Enter": 0x01000005,
    "Insert": 0x01000006, "Delete": 0x01000007, "Pause": 0x01000008,
    "Print": 0x01000009, "Sys_Req": 0x0100000A, "Home": 0x01000010,
    "End": 0x01000011, "Left": 0x01000012, "Up": 0x01000013,
    "Right": 0x01000014, "Down": 0x01000015, "Page_Up": 0x01000016,
    "Page_Down": 0x01000017, "Menu": 0x01000055, "space": 0x20,
}
_QT_NAMED.update({f"F{n}": 0x01000030 + n - 1 for n in range(1, 36)})
_QT_NAMED_REV = {v: k for k, v in _QT_NAMED.items() if k != "ISO_Left_Tab"}


def desktop_id(action_id):
    return f"cosmicshot-{action_id.replace('_', '-')}.desktop"


# -- key translation --------------------------------------------------------
def _gdk():
    Gdk, km = shortcuts._keymap()
    return Gdk, km


def _keyval_to_qt(Gdk, keyval):
    name = Gdk.keyval_name(keyval) or ""
    if name in _QT_NAMED:
        return _QT_NAMED[name]
    u = Gdk.keyval_to_unicode(keyval)
    if u and u >= 0x20:
        return ord(chr(u).upper()[0])  # Qt::Key for Latin-1 is the upper-case code
    return None


def _shifted_keyval(Gdk, km, base_name):
    """The keysym the base key types with Shift held ('4' -> dollar)."""
    kv = Gdk.keyval_from_name(base_name)
    ok, keys = km.get_entries_for_keyval(kv)
    keys = [k for k in (keys if ok else []) if k.keycode <= 255 and k.level == 0]
    if not keys:
        return None
    k = keys[0]
    res = km.translate_keyboard_state(k.keycode, Gdk.ModifierType.SHIFT_MASK, k.group)
    return res[1] if res[0] else None


def accel_to_qt(accel):
    """'Ctrl+Shift+4' -> the int KWin will report for that press, or None."""
    mods, key = shortcuts.parse_accel(accel)
    if not key:
        return None
    Gdk, km = _gdk()
    if km is None:
        return None
    is_letter = len(key) == 1 and key.isalpha()
    if "Shift" in mods and not is_letter:
        kv = _shifted_keyval(Gdk, km, key)
        if kv is not None and Gdk.keyval_name(kv) != key:
            # KWin consumes Shift here and reports the shifted symbol.
            mods = [m for m in mods if m != "Shift"]
        else:
            kv = Gdk.keyval_from_name(key)
    else:
        kv = Gdk.keyval_from_name(key)
    qt = _keyval_to_qt(Gdk, kv)
    if qt is None:
        return None
    for name, bit in _QT_MODS:
        if name in mods:
            qt |= bit
    return qt


def qt_to_accel(qt):
    """KDE's int back to our form: Meta+% -> 'Super+Shift+5' (as pressed)."""
    mods = [name for name, bit in _QT_MODS if qt & bit]
    code = qt & ~_QT_MOD_MASK
    if code in _QT_NAMED_REV:
        return shortcuts.format_accel(mods, _QT_NAMED_REV[code])
    if not (0x20 <= code < 0x01000000):
        return None
    Gdk, _km = _gdk()
    ch = chr(code)
    if ch.isalpha():
        return shortcuts.format_accel(mods, ch.lower())
    name = Gdk.keyval_name(Gdk.unicode_to_keyval(code)) or ch
    base = shortcuts._unshift(name)
    if base != name and "Shift" not in mods:
        mods.append("Shift")
    order = [m for m, _b in _QT_MODS]
    return shortcuts.format_accel(sorted(mods, key=order.index), base)


# -- kglobalaccel over D-Bus ------------------------------------------------
def _call(method, signature, args, reply="()"):
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync(_BUS, _PATH, _IFACE, method,
                        GLib.Variant(signature, args) if signature else None,
                        GLib.VariantType(reply), Gio.DBusCallFlags.NONE, 5000, None)
    return res.unpack()


def available():
    try:
        _call("allMainComponents", None, None, "(aas)")
        return True
    except Exception:
        return False


def _action_id(action_id, label=""):
    return [desktop_id(action_id), _ACTION, "CosmicShot", label]


def current():
    """{action_id: [accel, ...]} for every CosmicShot action bound in KDE,
    including bindings made by hand in System Settings."""
    out = {}
    for aid, label, _cmd in config.SHORTCUT_ACTIONS:
        try:
            (keys,) = _call("shortcutKeys", "(as)", (_action_id(aid, label),), "(a(ai))")
        except Exception:
            continue
        accels = []
        for (seq,) in keys:
            if seq and seq[0]:
                a = qt_to_accel(seq[0])
                if a and a not in accels:
                    accels.append(a)
        if accels:
            out[aid] = accels
    return out


def _owner_of(qt, mine=None):
    """Who else in KDE already uses this key, as a readable name, or None.
    KDE does not refuse duplicates itself, so we check before binding."""
    try:
        (hits,) = _call("globalShortcutsByKey", "((ai)(i))", (([qt, 0, 0, 0],), (0,)),
                        "(a(ssssssaiai))")
    except Exception:
        return None
    for h in hits:
        # (uniqueName, friendlyName, componentUniqueName, componentFriendlyName, ...)
        if h[2] == mine:
            continue
        app = h[3] or h[2]
        act = h[1] or h[0]
        return app if (h[0] == _ACTION or act == app) else f"{app} › {act}"
    return None


def _write_desktop(action_id, label, command):
    path = _APPS / desktop_id(action_id)
    exe = shutil.which("cosmicshot")
    if exe:
        command = command.replace("cosmicshot", exe, 1)
    text = ("[Desktop Entry]\n"
            "Type=Application\n"
            f"Name=CosmicShot: {label}\n"
            f"Exec={command}\n"
            "Icon=cosmicshot\n"
            "NoDisplay=true\n"
            f"{_MARKER}\n")
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _refresh_menu_cache():
    tool = shutil.which("kbuildsycoca6")
    if tool:
        import subprocess
        subprocess.run([tool], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)


def set_shortcut(action_id, accel):
    """Bind (or with accel falsy, unbind) one action in KDE.
    Returns (ok, message)."""
    acts = {a: (label, cmd) for a, label, cmd in config.SHORTCUT_ACTIONS}
    label, command = acts[action_id]
    aid = _action_id(action_id, label)
    if not accel:
        _call("setShortcutKeys", "(asa(ai)u)", (aid, [], _SET_FLAGS), "(a(ai))")
        try:
            _call("unregister", "(ss)", (aid[0], _ACTION), "(b)")
        except Exception:
            pass
        path = _APPS / desktop_id(action_id)
        try:
            if path.exists() and _MARKER in path.read_text():
                path.unlink()  # only launchers we created; hand-made ones stay
                _refresh_menu_cache()
        except OSError:
            pass
        return True, "Removed from KDE."
    qt = accel_to_qt(accel)
    if qt is None:
        return False, f"KDE can't use {accel} as a shortcut."
    owner = _owner_of(qt, mine=aid[0])
    if owner:
        return False, f"{accel} is already used by {owner}."
    if _write_desktop(action_id, label, command):
        _refresh_menu_cache()
    _call("doRegister", "(as)", (aid,))
    (got,) = _call("setShortcutKeys", "(asa(ai)u)",
                   (aid, [([qt, 0, 0, 0],)], _SET_FLAGS), "(a(ai))")
    if not any(seq and seq[0] == qt for (seq,) in got):
        return False, f"KDE refused {accel}."
    return True, "Saved — it works right away."
