"""Inject scroll + pointer positioning via virtual uinput devices (python-evdev).

Auto-scroll needs to scroll another app's window — only possible on Wayland via
kernel-level input injection. Two SEPARATE virtual devices are used on purpose:

* a relative mouse (REL_X/Y/WHEEL) for scrolling — mixing ABS axes onto it makes
  libinput treat it as a tablet and drop the wheel, so they must stay separate;
* an optional absolute tablet (ABS_X/Y + pen) to park the pointer over the
  target so scrolling is independent of the user's real mouse — best-effort,
  since not every compositor honours it.

Requires write access to /dev/uinput (a seat ACL on COSMIC, or an input-group
udev rule). ``available()`` reports usability; everything degrades to manual
scroll if not. GTK-free.
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
        ui = UInput({e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES],
                     e.EV_KEY: [e.BTN_LEFT]},
                    name="cosmicshot-virtual-mouse")
        ui.close()
        return True
    except Exception:
        return False


class Scroller:
    """A relative virtual mouse that scrolls the window under the pointer, plus
    an optional absolute tablet to position the pointer first."""

    def __init__(self, desktop=None):
        from evdev import UInput, AbsInfo, ecodes as e
        self._e = e
        # Relative mouse — the reliable scroller. REL_X/Y make libinput classify
        # it as a mouse so the wheel is delivered as scroll.
        self._mouse = UInput(
            {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES],
             e.EV_KEY: [e.BTN_LEFT]},
            name="cosmicshot-virtual-mouse",
        )
        # Absolute tablet — best-effort pointer positioning, separate device.
        self._tablet = None
        if desktop:
            self._x0, self._y0, self._w, self._h = desktop
            try:
                self._tablet = UInput(
                    {e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, _ABS_MAX, 0, 0, 0)),
                                (e.ABS_Y, AbsInfo(0, 0, _ABS_MAX, 0, 0, 0))],
                     e.EV_KEY: [e.BTN_TOOL_PEN, e.BTN_TOUCH]},
                    name="cosmicshot-virtual-tablet",
                )
            except Exception:
                self._tablet = None
        time.sleep(0.4)  # let the compositor bind the device(s)

    def move_to(self, px: float, py: float) -> None:
        """Best-effort: park the pointer at global pixel (px, py)."""
        if self._tablet is None or not self._w or not self._h:
            return
        e = self._e
        ax = int(max(0.0, min(1.0, (px - self._x0) / self._w)) * _ABS_MAX)
        ay = int(max(0.0, min(1.0, (py - self._y0) / self._h)) * _ABS_MAX)
        # A hovering pen (tool present, not touching) moves the cursor.
        self._tablet.write(e.EV_KEY, e.BTN_TOOL_PEN, 1)
        self._tablet.write(e.EV_ABS, e.ABS_X, ax)
        self._tablet.write(e.EV_ABS, e.ABS_Y, ay)
        self._tablet.syn()
        time.sleep(0.05)

    def scroll(self, ticks: int) -> None:
        """Scroll by ``ticks`` notches; negative = down (content moves up)."""
        e = self._e
        step = -1 if ticks < 0 else 1
        for _ in range(abs(ticks)):
            self._mouse.write(e.EV_REL, e.REL_WHEEL, step)
            self._mouse.write(e.EV_REL, e.REL_WHEEL_HI_RES, step * 120)
            self._mouse.syn()
            time.sleep(0.008)

    def scroll_down(self, ticks: int) -> None:
        self.scroll(-abs(ticks))

    def close(self) -> None:
        for dev in (self._mouse, self._tablet):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass
