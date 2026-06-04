"""Inject scroll-wheel events via a virtual uinput device (python-evdev).

Used for auto-scrolling during a scrolling screenshot. Kernel-level injection
is the only way to scroll another app's window on Wayland; it requires write
access to /dev/uinput (granted by a seat ACL on COSMIC, or an input-group udev
rule). ``available()`` reports whether it works; everything degrades to manual
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
    """A virtual mouse that can scroll the window currently under the pointer."""

    def __init__(self):
        from evdev import UInput, ecodes as e
        self._e = e
        self._ui = UInput(
            {e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES], e.EV_KEY: [e.BTN_LEFT]},
            name="cosmicshot-virtual-mouse",
        )
        # Give the compositor a moment to bind the new device.
        time.sleep(0.3)

    def scroll(self, ticks: int) -> None:
        """Scroll by ``ticks`` notches; negative = down (content moves up)."""
        e = self._e
        for _ in range(abs(ticks)):
            step = -1 if ticks < 0 else 1
            self._ui.write(e.EV_REL, e.REL_WHEEL, step)
            self._ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, step * 120)
            self._ui.syn()
            time.sleep(0.01)

    def scroll_down(self, ticks: int) -> None:
        self.scroll(-abs(ticks))

    def close(self) -> None:
        try:
            self._ui.close()
        except Exception:
            pass
