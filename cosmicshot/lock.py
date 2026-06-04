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
_ACTIVE_PID = os.path.join(_RUNTIME, "cosmicshot-capture.pid")
_TRAY_PID = os.path.join(_RUNTIME, "cosmicshot-tray.pid")
_RECORDING = os.path.join(_RUNTIME, "cosmicshot-recording.pid")


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_pid(path) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _read_pid(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


# -- tray presence (so a recording can hand its Stop button to the panel) -----
def write_tray_pid() -> None:
    _write_pid(_TRAY_PID)


def tray_pid():
    pid = _read_pid(_TRAY_PID)
    return pid if _alive(pid) else None


# -- active recording (panel-controlled Stop) ---------------------------------
def write_recording_pid() -> None:
    _write_pid(_RECORDING)


def recording_pid():
    pid = _read_pid(_RECORDING)
    return pid if _alive(pid) else None


def clear_recording_pid() -> None:
    try:
        os.unlink(_RECORDING)
    except OSError:
        pass


def write_active_pid() -> None:
    """Record the PID of the process whose editor currently holds the screen,
    so a second capture can signal it to come to the front."""
    try:
        with open(_ACTIVE_PID, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def read_active_pid():
    try:
        with open(_ACTIVE_PID) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def clear_active_pid() -> None:
    try:
        os.unlink(_ACTIVE_PID)
    except OSError:
        pass


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
