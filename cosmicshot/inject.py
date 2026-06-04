"""Inject pointer motion + scroll via a virtual uinput device (python-evdev).

Used for auto-scrolling during a scrolling screenshot. Kernel-level injection is
the only way to scroll another app's window on Wayland. We also position the
pointer absolutely over the target first, so scrolling is independent of where
the user's real mouse is. Requires write access to /dev/uinput (a seat ACL on
COSMIC, or an input-group udev rule). ``available()`` reports usability;
everything degrades to manual scroll if not. GTK-free.
"""
from __future__ import annotations

import time

_ABS_MAX = 65535


def available() -> bool:
    try:
        from evdev import UInput, ecodes as e
    except Exception:
        return False
    try:
        ui = UInput({e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES],
                     e.EV_KEY: [e.BTN_LEFT]},
                    name="cosmicshot-virtual-mouse")
        ui.close()
        return True
    except Exception:
        return False


class Scroller:
    """A virtual pointer that can be positioned absolutely and scroll the window
    under it. ``desktop`` = (x0, y0, width, height) of the whole layout, enabling
    absolute moves; without it, scroll targets wherever the real pointer is."""

    def __init__(self, desktop=None):
        from evdev import UInput, AbsInfo, ecodes as e
        self._e = e
        caps = {e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES], e.EV_KEY: [e.BTN_LEFT]}
        self._abs = bool(desktop)
        if self._abs:
            self._x0, self._y0, self._w, self._h = desktop
            caps[e.EV_ABS] = [
                (e.ABS_X, AbsInfo(0, 0, _ABS_MAX, 0, 0, 0)),
                (e.ABS_Y, AbsInfo(0, 0, _ABS_MAX, 0, 0, 0)),
            ]
        self._ui = UInput(caps, name="cosmicshot-virtual-pointer")
        time.sleep(0.3)  # let the compositor bind the device

    def move_to(self, px: float, py: float) -> None:
        """Move the pointer to global pixel (px, py) (no-op without desktop)."""
        if not self._abs or not self._w or not self._h:
            return
        e = self._e
        ax = int(max(0.0, min(1.0, (px - self._x0) / self._w)) * _ABS_MAX)
        ay = int(max(0.0, min(1.0, (py - self._y0) / self._h)) * _ABS_MAX)
        self._ui.write(e.EV_ABS, e.ABS_X, ax)
        self._ui.write(e.EV_ABS, e.ABS_Y, ay)
        self._ui.syn()
        time.sleep(0.03)

    def scroll(self, ticks: int) -> None:
        """Scroll by ``ticks`` notches; negative = down (content moves up)."""
        e = self._e
        step = -1 if ticks < 0 else 1
        for _ in range(abs(ticks)):
            self._ui.write(e.EV_REL, e.REL_WHEEL, step)
            self._ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, step * 120)
            self._ui.syn()
            time.sleep(0.008)

    def scroll_down(self, ticks: int) -> None:
        self.scroll(-abs(ticks))

    def close(self) -> None:
        try:
            self._ui.close()
        except Exception:
            pass
