"""Inject scroll-wheel events via a virtual uinput device (python-evdev).

Auto-scroll needs to scroll another app's window — only possible on Wayland via
kernel-level input injection. We use a single relative mouse and scroll the
window currently under the pointer.

NOTE on positioning: absolute pointer positioning (an ABS/tablet device) is NOT
reliable on COSMIC — its multi-output/scaled mapping warps the pointer to the
wrong place, which breaks scrolling entirely. So we do NOT move the pointer; the
caller relies on the pointer already being over the target (it is, right after
picking a window). ``move_to`` is a no-op kept for API compatibility.

Requires write access to /dev/uinput (a seat ACL on COSMIC, or an input-group
udev rule). ``available()`` reports usability; everything degrades to manual
scroll if not. GTK-free.
"""
from __future__ import annotations

import time


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
    """A relative virtual mouse that scrolls the window under the pointer."""

    def __init__(self, desktop=None):
        from evdev import UInput, ecodes as e
        self._e = e
        self._ui = UInput(
            {e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES], e.EV_KEY: [e.BTN_LEFT]},
            name="cosmicshot-virtual-mouse",
        )
        time.sleep(0.4)  # let the compositor bind the device

    def move_to(self, px: float, py: float) -> None:
        """No-op: see the module note — ABS positioning is unreliable on COSMIC
        and breaks scrolling, so we leave the pointer where it is."""
        return

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
