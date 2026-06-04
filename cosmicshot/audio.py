"""Audio source discovery + a quick chooser dialog for recordings.

Recording sound is opt-in: the dialog defaults to "No sound". The user can pick
the PC output (the default sink's monitor source) or any connected microphone.
Sources are read from PipeWire/PulseAudio via ``pactl``.
"""
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402


def _pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True,
                              text=True, timeout=3).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def list_sources():
    """Return [(label, device_name_or_None)] — always starts with "No sound".

    System sound = the default sink's ``.monitor`` source; microphones are the
    non-monitor capture sources.
    """
    choices = [("No sound", None)]
    default_sink = _pactl("get-default-sink").strip()

    # name -> human description (from the verbose listing)
    desc, name = {}, None
    for line in _pactl("list", "sources").splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            name = s[5:].strip()
        elif s.startswith("Description:") and name:
            desc[name] = s[len("Description:"):].strip()

    names = []
    for line in _pactl("list", "short", "sources").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.append(parts[1])

    # System sound: prefer the default sink's monitor, else the first monitor.
    monitors = [n for n in names if n.endswith(".monitor")]
    sysmon = next((n for n in monitors if n == default_sink + ".monitor"),
                  monitors[0] if monitors else None)
    if sysmon:
        choices.append(("System sound (PC output)", sysmon))

    for n in names:
        if not n.endswith(".monitor"):
            label = desc.get(n, n)
            choices.append((f"Microphone: {label}", n))
    return choices


def choose_source(title="Record sound?"):
    """Modal chooser. Returns (proceed: bool, device: str | None).

    device is None for a silent recording. proceed is False if the user
    cancelled (the recording should not start)."""
    sources = list_sources()
    dlg = Gtk.Dialog(title=title)
    dlg.set_modal(True)
    dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    start = dlg.add_button("Start recording", Gtk.ResponseType.OK)
    start.get_style_context().add_class("suggested-action")
    dlg.set_default_response(Gtk.ResponseType.OK)

    box = dlg.get_content_area()
    box.set_spacing(10)
    box.set_margin_top(16)
    box.set_margin_bottom(16)
    box.set_margin_start(18)
    box.set_margin_end(18)
    box.add(Gtk.Label(label="Record audio with this video?", xalign=0))
    combo = Gtk.ComboBoxText()
    for label, _dev in sources:
        combo.append_text(label)
    combo.set_active(0)  # "No sound" by default
    box.add(combo)
    dlg.show_all()

    resp = dlg.run()
    idx = combo.get_active()
    dlg.destroy()
    if resp != Gtk.ResponseType.OK:
        return (False, None)
    device = sources[idx][1] if 0 <= idx < len(sources) else None
    return (True, device)
