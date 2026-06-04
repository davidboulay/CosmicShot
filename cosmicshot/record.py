"""Screen/window/region video recording via the ScreenCast portal + GStreamer.

COSMIC implements ext-image-copy-capture (not wlr-screencopy), so wf-recorder &
co. don't work. The sanctioned path is the freedesktop ScreenCast portal: it
hands us a PipeWire node we encode with GStreamer. The portal also natively
picks a monitor or a window (so App Window / Screen need no overlay of ours);
Region records the chosen monitor and crops to the rectangle.

Flow: CreateSession -> SelectSources(type) -> Start (user consents in the
compositor's picker) -> OpenPipeWireRemote (fd) -> pipewiresrc -> H.264 -> mp4.
"""
from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

_BUS = "org.freedesktop.portal.Desktop"
_OBJ = "/org/freedesktop/portal/desktop"
_SC = "org.freedesktop.portal.ScreenCast"
_REQ = "org.freedesktop.portal.Request"

# SelectSources types bitmask / cursor modes (portal spec).
SOURCE_MONITOR = 1
SOURCE_WINDOW = 2
CURSOR_EMBEDDED = 2

_Gst_inited = False


def _gst():
    global _Gst_inited
    if not _Gst_inited:
        Gst.init(None)
        _Gst_inited = True


def _have(element: str) -> bool:
    return Gst.ElementFactory.find(element) is not None


class ScreenCastPortal:
    """Runs the ScreenCast portal handshake on the default GLib main context."""

    def __init__(self, source_type: int):
        self._type = source_type
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        self._sender = sender
        self._n = 0
        self._session = None
        self._on_ready = None
        self._on_error = None

    # -- request/response plumbing ---------------------------------------
    def _token(self):
        self._n += 1
        return f"cs{self._n}"

    def _request(self, method, args, options, on_response, fd_list=None):
        token = self._token()
        options["handle_token"] = GLib.Variant("s", token)
        path = f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"
        state = {}

        def handler(_c, _s, _p, _i, _sig, params):
            self._bus.signal_unsubscribe(state["sub"])
            code, results = params.unpack()
            on_response(code, results)

        state["sub"] = self._bus.signal_subscribe(
            _BUS, _REQ, "Response", path, None, Gio.DBusSignalFlags.NONE, handler)

        full = list(args) + [options]
        variant = GLib.Variant(self._sig(method), tuple(full))
        self._bus.call_with_unix_fd_list(
            _BUS, _OBJ, _SC, method, variant, GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE, -1, fd_list, None, lambda *_: None)

    @staticmethod
    def _sig(method):
        return {
            "CreateSession": "(a{sv})",
            "SelectSources": "(oa{sv})",
            "Start": "(osa{sv})",
        }[method]

    # -- handshake steps -------------------------------------------------
    def start(self, on_ready, on_error):
        self._on_ready, self._on_error = on_ready, on_error
        opts = {"session_handle_token": GLib.Variant("s", self._token())}
        try:
            self._request("CreateSession", [], opts, self._created)
        except Exception as exc:
            on_error(f"portal CreateSession failed: {exc}")

    def _created(self, code, results):
        if code != 0 or "session_handle" not in results:
            return self._on_error("Recording was cancelled.")
        self._session = results["session_handle"]
        opts = {
            "types": GLib.Variant("u", self._type),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
        }
        self._request("SelectSources", [GLib.Variant("o", self._session)],
                      opts, self._selected)

    def _selected(self, code, _results):
        if code != 0:
            return self._on_error("Recording was cancelled.")
        self._request("Start", [GLib.Variant("o", self._session),
                                GLib.Variant("s", "")], {}, self._started)

    def _started(self, code, results):
        if code != 0:
            return self._on_error("Recording was cancelled.")
        streams = results.get("streams") or []
        if not streams:
            return self._on_error("No screen source was selected.")
        node_id, props = streams[0]
        self._open_pipewire(node_id, props)

    def _open_pipewire(self, node_id, props):
        variant = GLib.Variant("(oa{sv})",
                               (self._session, {}))
        self._bus.call_with_unix_fd_list(
            _BUS, _OBJ, _SC, "OpenPipeWireRemote", variant,
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
            self._pw_opened, (node_id, props))

    def _pw_opened(self, conn, res, user_data):
        node_id, props = user_data
        try:
            ret, fd_list = conn.call_with_unix_fd_list_finish(res)
            idx = ret.unpack()[0]
            fd = fd_list.get(idx)
        except Exception as exc:
            return self._on_error(f"OpenPipeWireRemote failed: {exc}")
        self._on_ready(fd, node_id, props)


class Recorder:
    """Encodes a PipeWire screencast node to an H.264 mp4, with optional crop."""

    def __init__(self, out_path: str):
        _gst()
        self.out_path = out_path
        self.pipeline = None
        self._loop_quit = None

    def _encoder_chain(self):
        # Prefer hardware VAAPI; fall back to software openh264.
        if _have("vah264enc"):
            return "vah264enc ! h264parse"
        if _have("openh264enc"):
            return "openh264enc ! h264parse"
        if _have("x264enc"):
            return "x264enc tune=zerolatency speed-preset=veryfast ! h264parse"
        raise RuntimeError("No H.264 encoder (install gstreamer1.0-vaapi or "
                           "gstreamer1.0-plugins-ugly).")

    def build(self, fd: int, node_id: int, crop=None):
        crop_str = ""
        if crop:
            left, top, right, bottom = crop
            crop_str = (f"videocrop top={top} left={left} right={right} "
                        f"bottom={bottom} ! ")
        desc = (
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 "
            f"! videorate ! video/x-raw,framerate=30/1 ! videoconvert ! "
            f"{crop_str}{self._encoder_chain()} ! "
            f"mp4mux faststart=true ! filesink location={GLib.shell_quote(self.out_path)}"
        )
        self.pipeline = Gst.parse_launch(desc)

    def play(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        """Send EOS and wait for the mux to finalise the mp4, then tear down."""
        if self.pipeline is None:
            return
        self.pipeline.send_event(Gst.Event.new_eos())
        bus = self.pipeline.get_bus()
        bus.timed_pop_filtered(8 * Gst.SECOND,
                               Gst.MessageType.EOS | Gst.MessageType.ERROR)
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None


class RecordingSession:
    """Drives a recording end to end: portal handshake, encode, a small control
    card (● REC + timer + Stop/Cancel), and clean finalisation. Returns the
    saved path from run(), or None if cancelled/failed."""

    def __init__(self, target, out_path, region=None, monitors=None):
        self.target = target              # "screen" | "window" | "region"
        self.out_path = out_path
        self.region = region              # (x, y, w, h) for region target
        self.monitors = monitors or []
        self.recorder = None
        self.error = None
        self.saved = None
        self._elapsed = 0
        self._timer_id = None
        self._started = False
        self.ctrl = None
        self._timer_lbl = None

    # -- control card ----------------------------------------------------
    def _build_control(self):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell

        win = Gtk.Window()
        GtkLayerShell.init_for_window(win)
        mon, edge, margin = self._place()
        if mon is not None:
            GtkLayerShell.set_monitor(win, mon)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        if edge is not None:
            GtkLayerShell.set_anchor(win, edge, True)
            GtkLayerShell.set_margin(win, edge, max(8, int(margin)))
        GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.EXCLUSIVE)

        prov = Gtk.CssProvider()
        prov.load_from_data(
            b".rec-card{background-color:rgba(20,22,28,0.96);border:2px solid #ff4444;"
            b"border-radius:14px;padding:12px 18px;} .rec-card label{color:#fff;"
            b"font-size:15px;font-weight:bold;} .rec-card button{padding:4px 14px;"
            b"border-radius:9px;font-weight:bold;} .rec-stop{background-image:none;"
            b"background-color:#ff4444;color:#fff;}")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.get_style_context().add_class("rec-card")
        box.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win.add(box)
        self._timer_lbl = Gtk.Label(label="Choose what to record…")
        self._timer_lbl.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.pack_start(self._timer_lbl, False, False, 0)
        stop = Gtk.Button(label="Stop & Save")
        stop.get_style_context().add_class("rec-stop")
        stop.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        stop.connect("clicked", lambda _b: self.stop())
        box.pack_start(stop, False, False, 0)
        cancel = Gtk.Button(label="Cancel (Esc)")
        cancel.connect("clicked", lambda _b: self.cancel())
        box.pack_start(cancel, False, False, 0)
        win.connect("key-press-event", self._on_key)
        self.ctrl = win
        win.show_all()

    def _place(self):
        """Keep the card off the recorded area: for a region, below/above it;
        for screen, on another monitor; for window, anywhere (the window-capture
        doesn't include our overlay)."""
        from gi.repository import Gdk, GtkLayerShell
        disp = Gdk.Display.get_default()

        def gdkmon(m):
            return disp.get_monitor(m.index)

        if self.target == "region" and self.region and self.monitors:
            rx, ry, rw, rh = self.region
            cx, cy = rx + rw / 2, ry + rh / 2
            host = next((m for m in self.monitors
                         if m.x <= cx < m.x + m.width and m.y <= cy < m.y + m.height),
                        self.monitors[0])
            below = (ry + rh - host.y) + 14
            if below + 64 <= host.height:
                return gdkmon(host), GtkLayerShell.Edge.TOP, below
            above = (ry - host.y) - 14 - 64
            if above >= 8:
                return gdkmon(host), GtkLayerShell.Edge.TOP, above
            return gdkmon(host), GtkLayerShell.Edge.BOTTOM, 8
        # window: anywhere; screen: prefer a non-primary monitor.
        if self.target == "screen" and len(self.monitors) > 1:
            other = next((m for m in self.monitors if not m.primary), self.monitors[-1])
            return gdkmon(other), None, 0
        return None, GtkLayerShell.Edge.BOTTOM, 60

    def _on_key(self, _w, ev):
        from gi.repository import Gdk
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.cancel()
        return True

    # -- lifecycle -------------------------------------------------------
    def run(self):
        from gi.repository import Gtk
        self._build_control()
        stype = SOURCE_WINDOW if self.target == "window" else SOURCE_MONITOR
        self._portal = ScreenCastPortal(stype)
        self._portal.start(self._on_ready, self._on_error)
        Gtk.main()
        return self.saved

    def _on_ready(self, fd, node_id, props):
        crop = None
        if self.target == "region" and self.region:
            crop = self._crop_for(props)
        try:
            self.recorder = Recorder(self.out_path)
            self.recorder.build(fd, node_id, crop)
            self.recorder.play()
        except Exception as exc:
            return self._on_error(str(exc))
        self._started = True
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)
        self._update()

    def _crop_for(self, props):
        pos = props.get("position")
        size = props.get("size")
        if not pos or not size:
            return None
        rx, ry, rw, rh = self.region
        left = max(0, int(rx - pos[0]))
        top = max(0, int(ry - pos[1]))
        right = max(0, int((pos[0] + size[0]) - (rx + rw)))
        bottom = max(0, int((pos[1] + size[1]) - (ry + rh)))
        return (left, top, right, bottom)

    def _tick(self):
        self._elapsed += 1
        self._update()
        return True

    def _update(self):
        if not self._timer_lbl:
            return
        if self._started:
            m, s = divmod(self._elapsed, 60)
            self._timer_lbl.set_markup(
                f"<span foreground='#ff5555'>●</span> <b>REC  {m:02d}:{s:02d}</b>")
        else:
            self._timer_lbl.set_text("Choose what to record…")

    def stop(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id); self._timer_id = None
        if self.recorder:
            self.recorder.stop()
            self.saved = self.out_path
        self._teardown()

    def cancel(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id); self._timer_id = None
        if self.recorder:
            self.recorder.stop()
        import os
        try:
            if os.path.exists(self.out_path):
                os.unlink(self.out_path)   # cancel discards the file
        except OSError:
            pass
        self.saved = None
        self._teardown()

    def _on_error(self, msg):
        self.error = msg
        self._teardown()

    def _teardown(self):
        from gi.repository import Gtk
        if self.ctrl is not None:
            self.ctrl.destroy()
        GLib.idle_add(Gtk.main_quit)
