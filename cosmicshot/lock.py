"""Cross-process locks via flock on files in the runtime dir.

Two uses:
  * SingleInstance("tray") — keep only one tray process (so relaunching the app
    from the dock is idempotent).
  * SingleInstance("capture") — only one capture/editor session at a time, so
    capture commands can't pile up while an editor is open.
"""
import fcntl
import os

_RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


class SingleInstance:
    def __init__(self, name):
        self.path = os.path.join(_RUNTIME, f"cosmicshot-{name}.lock")
        self._fh = None

    def acquire(self) -> bool:
        """Try to take the lock. Returns False if another process holds it.
        The lock is held until release() or process exit."""
        fh = open(self.path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False
