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
# Pin the GTK stack to 3.x so the lazy `from gi.repository import Gtk/Gdk` calls
# in this module don't grab GTK 4 (which would clash with the loaded Gdk 3.0).
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
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



class _RegionDim:
    """Dim everything except the recorded region (per monitor, click-through),
    so the user sees what's being captured. Outside the region crop, so it's
    never in the video."""

    def __init__(self, region, monitors):
        self.windows = []
        from gi.repository import Gdk
        from .overlay import _DimWindow
        disp = Gdk.Display.get_default()
        for m in monitors:
            try:
                w = _DimWindow(m, disp.get_monitor(m.index), region)
                self.windows.append(w); w.show_all()
            except Exception:
                pass

    def destroy(self):
        for w in self.windows:
            try:
                w.destroy()
            except Exception:
                pass
        self.windows = []


class PreviewWindow:
    """Plays the just-recorded clip and offers Save / Discard. Closing without
    saving asks for confirmation."""

    def __init__(self, path, on_save, on_discard):
        from gi.repository import Gtk
        _gst()
        self.path = path
        self._on_save = on_save
        self._on_discard = on_discard
        self.win = Gtk.Window(title="CosmicShot — Recording")
        self.win.set_default_size(900, 560)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.connect("delete-event", self._on_close)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win.add(box)

        self._playbin = Gst.ElementFactory.make("playbin", None)
        sink = Gst.ElementFactory.make("gtksink", None)
        video = None
        if sink is not None:
            self._playbin.set_property("video-sink", sink)
            video = sink.get_property("widget")
        self._playbin.set_property("uri", "file://" + path)
        if video is not None:
            box.pack_start(video, True, True, 0)
        else:
            box.pack_start(Gtk.Label(label="(preview unavailable)"), True, True, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_top(8); bar.set_margin_bottom(8)
        bar.set_margin_start(10); bar.set_margin_end(10)
        self._play_btn = Gtk.Button(label="⏸ Pause")
        self._play_btn.connect("clicked", self._toggle_play)
        bar.pack_start(self._play_btn, False, False, 0)
        bar.pack_end(self._save_btn(), False, False, 0)
        discard = Gtk.Button(label="Discard")
        discard.get_style_context().add_class("destructive-action")
        discard.connect("clicked", lambda _b: self._discard())
        bar.pack_end(discard, False, False, 0)
        box.pack_start(bar, False, False, 0)

        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message::eos", self._loop)
        self._bus.connect("message::error", lambda *_: None)

    def _save_btn(self):
        from gi.repository import Gtk
        b = Gtk.Button(label="Save")
        b.get_style_context().add_class("suggested-action")
        b.connect("clicked", lambda _b: self._save())
        return b

    def _toggle_play(self, _b):
        ok, state, _ = self._playbin.get_state(0)
        if state == Gst.State.PLAYING:
            self._playbin.set_state(Gst.State.PAUSED); self._play_btn.set_label("▶ Play")
        else:
            self._playbin.set_state(Gst.State.PLAYING); self._play_btn.set_label("⏸ Pause")

    def _loop(self, *_):
        self._playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)

    def _stop_player(self):
        try:
            self._playbin.set_state(Gst.State.NULL)
        except Exception:
            pass

    def _save(self):
        self._stop_player()
        self.win.destroy()
        self._on_save()

    def _discard(self):
        from gi.repository import Gtk
        dlg = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK_CANCEL,
                                text="Discard this recording?")
        dlg.format_secondary_text("The clip will be deleted and not saved.")
        resp = dlg.run(); dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            self._stop_player()
            self.win.destroy()
            self._on_discard()

    def _on_close(self, *_):
        # Closing the window = discard, with a warning.
        self._discard()
        return True

    def run(self):
        self.win.show_all()
        self._playbin.set_state(Gst.State.PLAYING)


class RecordingSession:
    """Drives a recording end to end: portal handshake, encode to a temp file,
    a ● REC control card (timer + Stop/Cancel) placed off the recorded area,
    then a preview window to Save or Discard. run() returns the saved path or
    None."""

    def __init__(self, target, save_dir, region=None, monitors=None):
        import os
        import time
        self.target = target
        self.save_dir = save_dir
        self.region = region
        self.monitors = monitors or []
        os.makedirs(save_dir, exist_ok=True)
        stamp = time.strftime("CosmicShot_%Y-%m-%d_%H-%M-%S")
        self.final_path = os.path.join(save_dir, stamp + ".mp4")
        self._tmp = os.path.join(save_dir, "." + stamp + ".recording.mp4")
        self.recorder = None
        self.error = None
        self.saved = None
        self._elapsed = 0
        self._timer_id = None
        self._started = False
        self.ctrl = None
        self._timer_lbl = None
        self._dim = None

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
        stop = Gtk.Button(label="Stop")
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
        if self.target == "region" and self.region:
            self._dim = _RegionDim(self.region, self.monitors)
        stype = SOURCE_WINDOW if self.target == "window" else SOURCE_MONITOR
        self._portal = ScreenCastPortal(stype)
        self._portal.start(self._on_ready, self._on_error)
        Gtk.main()
        return self.saved

    def _on_ready(self, fd, node_id, props):
        crop = self._crop_for(props) if (self.target == "region" and self.region) else None
        try:
            self.recorder = Recorder(self._tmp)
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

    def _close_overlays(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id); self._timer_id = None
        if self._dim is not None:
            self._dim.destroy(); self._dim = None
        if self.ctrl is not None:
            self.ctrl.destroy(); self.ctrl = None

    def stop(self):
        import os
        self._close_overlays()
        if self.recorder is None:
            return self._quit()
        self.recorder.stop()
        if not os.path.exists(self._tmp):
            return self._quit()
        # Preview: let the user watch it, then Save or Discard.
        prev = PreviewWindow(self._tmp, self._save_temp, self._discard_temp)
        prev.run()

    def _save_temp(self):
        import os
        try:
            os.replace(self._tmp, self.final_path)
            self.saved = self.final_path
        except OSError:
            self.saved = self._tmp
        self._quit()

    def _discard_temp(self):
        import os
        try:
            os.unlink(self._tmp)
        except OSError:
            pass
        self.saved = None
        self._quit()

    def cancel(self):
        import os
        self._close_overlays()
        if self.recorder:
            self.recorder.stop()
        try:
            if os.path.exists(self._tmp):
                os.unlink(self._tmp)
        except OSError:
            pass
        self.saved = None
        self._quit()

    def _on_error(self, msg):
        self.error = msg
        self._close_overlays()
        self._quit()

    def _quit(self):
        from gi.repository import Gtk
        GLib.idle_add(Gtk.main_quit)
