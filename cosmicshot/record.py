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

import sys

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
            try:
                code, results = params.unpack()
                on_response(code, results)
            except Exception as exc:  # never leave the main loop hung on a stuck
                import traceback      # handshake (it holds the capture lock)
                traceback.print_exc()
                if self._on_error:
                    self._on_error(f"portal handshake failed: {exc}")

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
        self._request("SelectSources", [self._session], opts, self._selected)

    def _selected(self, code, _results):
        if code != 0:
            return self._on_error("Recording was cancelled.")
        self._request("Start", [self._session, ""], {}, self._started)

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
        self._crop = None
        self._crop_fraction = None

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

    def _audio_chain(self):
        """A pulsesrc -> AAC branch feeding the named mux, or None if no AAC
        encoder is installed (then we record video-only)."""
        if _have("avenc_aac"):
            enc = "avenc_aac"
        elif _have("voaacenc"):
            enc = "voaacenc"
        else:
            print("[record] no AAC encoder — recording without audio",
                  file=sys.stderr, flush=True)
            return None
        parse = "aacparse ! " if _have("aacparse") else ""
        return (f"pulsesrc name=asrc do-timestamp=true ! queue ! audioconvert ! "
                f"audioresample ! {enc} ! {parse}queue ! mux.")

    def build(self, fd: int, node_id: int, audio_device=None):
        # videocrop is always present (pass-through when not cropping). For a
        # region we set its borders from the ACTUAL negotiated frame size once
        # caps are known (see set_crop_fraction) — the portal reports a logical
        # size but the PipeWire frames arrive at device resolution, so a crop in
        # logical coords would land in the wrong place on scaled displays.
        audio = self._audio_chain() if audio_device else None
        desc = (
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 "
            f"! videorate ! video/x-raw,framerate=30/1 ! videoconvert ! "
            f"videocrop name=crop ! {self._encoder_chain()} ! "
            f"mp4mux name=mux faststart=true ! filesink name=sink"
        )
        if audio:
            desc += " " + audio
        self.pipeline = Gst.parse_launch(desc)
        # Set the path on the element (NOT in the parse string): parse_launch is
        # not a shell, so any quoting/spaces in the path would corrupt it.
        self.pipeline.get_by_name("sink").set_property("location", self.out_path)
        if audio:
            self.pipeline.get_by_name("asrc").set_property("device", audio_device)
        self._crop = self.pipeline.get_by_name("crop")

    def set_crop_fraction(self, fraction):
        """Crop to a region given as (fx, fy, fw, fh) fractions of the recorded
        monitor. Applied to the real frame size once the caps negotiate."""
        self._crop_fraction = fraction
        pad = self._crop.get_static_pad("sink")
        pad.connect("notify::caps", self._apply_crop)
        self._apply_crop(pad, None)  # in case caps are already set

    def _apply_crop(self, pad, _pspec):
        if not self._crop_fraction:
            return
        caps = pad.get_current_caps()
        if caps is None or caps.get_size() == 0:
            return
        s = caps.get_structure(0)
        ok_w, W = s.get_int("width")
        ok_h, H = s.get_int("height")
        if not (ok_w and ok_h) or W <= 0 or H <= 0:
            return
        fx, fy, fw, fh = self._crop_fraction
        left = max(0, min(W - 2, round(fx * W)))
        top = max(0, min(H - 2, round(fy * H)))
        width = max(2, round(fw * W))
        height = max(2, round(fh * H))
        right = max(0, W - left - width)
        bottom = max(0, H - top - height)
        # H.264 needs even dimensions; nudge the far edge if the kept area is odd.
        if (W - left - right) % 2:
            right += 1
        if (H - top - bottom) % 2:
            bottom += 1
        self._crop.set_property("left", left)
        self._crop.set_property("top", top)
        self._crop.set_property("right", right)
        self._crop.set_property("bottom", bottom)
        print(f"[record] crop applied on {W}x{H} frame: "
              f"left={left} top={top} right={right} bottom={bottom}",
              file=sys.stderr, flush=True)
        self._crop_fraction = None  # apply once

    def play(self):
        # Watch the bus so an encoder/negotiation error during recording is
        # surfaced instead of silently producing an empty file.
        self.error = None
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::warning", self._on_bus_warning)
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print(f"[record] pipeline -> PLAYING: {ret.value_nick}", file=sys.stderr, flush=True)

    def _on_bus_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        self.error = err.message
        print(f"[record] GST ERROR: {err.message} | {dbg}", file=sys.stderr, flush=True)

    def _on_bus_warning(self, _bus, msg):
        err, dbg = msg.parse_warning()
        print(f"[record] GST WARN: {err.message} | {dbg}", file=sys.stderr, flush=True)

    def stop(self) -> None:
        """Send EOS and wait for the mux to finalise the mp4, then tear down."""
        if self.pipeline is None:
            return
        # If the pipeline never reached PLAYING (e.g. an error), there's no EOS
        # coming — tear down immediately instead of blocking for 8s.
        _ok, state, _pending = self.pipeline.get_state(0)
        if self.error or state != Gst.State.PLAYING:
            print(f"[record] not PLAYING ({state.value_nick}) — skipping EOS wait",
                  file=sys.stderr, flush=True)
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            return
        print("[record] sending EOS, waiting for mux to finalise…", file=sys.stderr, flush=True)
        self.pipeline.send_event(Gst.Event.new_eos())
        bus = self.pipeline.get_bus()
        msg = bus.timed_pop_filtered(8 * Gst.SECOND,
                                     Gst.MessageType.EOS | Gst.MessageType.ERROR)
        print(f"[record] EOS wait result: {msg.type.value_nicks if msg else 'TIMEOUT'}",
              file=sys.stderr, flush=True)
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None



class _RegionDim:
    """Dim everything except the recorded region (per monitor, click-through),
    with a red frame around the region, so the user keeps seeing what's being
    captured. Click-through, so windows underneath stay usable; the dim/frame
    sit outside the crop, so they're never in the video."""

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

    def __init__(self, path, on_save, on_discard, suggested_name="recording.mp4",
                 start_dir=None):
        from gi.repository import Gtk
        _gst()
        self.path = path
        self._on_save = on_save
        self._on_discard = on_discard
        self._suggested_name = suggested_name
        self._start_dir = start_dir
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
        from gi.repository import Gtk
        dlg = Gtk.FileChooserDialog(
            title="Save recording", transient_for=self.win,
            action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Save", Gtk.ResponseType.ACCEPT)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name(self._suggested_name)
        if self._start_dir:
            try:
                dlg.set_current_folder(self._start_dir)
            except Exception:
                pass
        flt = Gtk.FileFilter()
        flt.set_name("MP4 video")
        flt.add_pattern("*.mp4")
        dlg.add_filter(flt)
        resp = dlg.run()
        chosen = dlg.get_filename() if resp == Gtk.ResponseType.ACCEPT else None
        dlg.destroy()
        if not chosen:
            return  # cancelled — keep the preview open so they can try again
        if not chosen.lower().endswith(".mp4"):
            chosen += ".mp4"
        self._stop_player()
        self.win.destroy()
        self._on_save(chosen)

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

    def present(self):
        """Raise the preview to the front (used when a new capture is attempted
        while this one is still waiting to be saved)."""
        try:
            self.win.present()
        except Exception:
            pass

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
        self.audio_device = None
        self._elapsed = 0
        self._timer_id = None
        self._started = False
        self.ctrl = None
        self._timer_lbl = None
        self._dim = None
        self._panel_mode = False
        self._sig_id = None
        self._present_sig = None

    # -- control card ----------------------------------------------------
    def _build_control(self):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell

        win = Gtk.Window()
        GtkLayerShell.init_for_window(win)
        mon, anchors = self._place()
        if mon is not None:
            GtkLayerShell.set_monitor(win, mon)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        for edge, margin in anchors:
            GtkLayerShell.set_anchor(win, edge, True)
            GtkLayerShell.set_margin(win, edge, max(0, int(margin)))
        # ON_DEMAND (not EXCLUSIVE): the control takes the keyboard only while
        # it's focused, so panel menus / other windows can still be opened during
        # a recording. EXCLUSIVE held a global keyboard grab that dismissed any
        # menu the moment it tried to open.
        GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.ON_DEMAND)

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
        """Return (gdk_monitor, [(Edge, margin), ...]) placing the control card
        OFF the recorded area: below the region, else above, else to the side;
        for screen recording, on a different monitor when there is one."""
        from gi.repository import Gdk, GtkLayerShell
        disp = Gdk.Display.get_default()
        E = GtkLayerShell.Edge

        def gdkmon(m):
            return disp.get_monitor(m.index)

        if self.target == "region" and self.region and self.monitors:
            rx, ry, rw, rh = self.region
            cx, cy = rx + rw / 2, ry + rh / 2
            host = next((m for m in self.monitors
                         if m.x <= cx < m.x + m.width and m.y <= cy < m.y + m.height),
                        self.monitors[0])
            CARD_H, CARD_W, GAP = 64, 320, 14
            below = (ry + rh - host.y) + GAP
            if below + CARD_H <= host.height:
                return gdkmon(host), [(E.TOP, below)]
            above = (ry - host.y) - GAP - CARD_H
            if above >= 8:
                return gdkmon(host), [(E.TOP, above)]
            # Region fills the height: put the card to whichever side has room,
            # vertically centred on the region.
            vmargin = max(8, min(int((ry - host.y) + rh / 2 - CARD_H / 2),
                                 host.height - CARD_H - 8))
            right_space = (host.x + host.width) - (rx + rw)
            left_space = rx - host.x
            if right_space >= CARD_W + GAP:
                return gdkmon(host), [(E.TOP, vmargin), (E.RIGHT, right_space - CARD_W - GAP)]
            if left_space >= CARD_W + GAP:
                return gdkmon(host), [(E.TOP, vmargin), (E.LEFT, left_space - CARD_W - GAP)]
            return gdkmon(host), [(E.BOTTOM, 8)]
        if self.target == "screen" and len(self.monitors) > 1:
            other = next((m for m in self.monitors if not m.primary), self.monitors[-1])
            return gdkmon(other), []
        return None, [(E.BOTTOM, 60)]

    def _on_key(self, _w, ev):
        from gi.repository import Gdk
        if ev.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.cancel()
        return True

    # -- lifecycle -------------------------------------------------------
    def run(self):
        from gi.repository import Gtk
        from . import audio
        # Ask for an audio source up front (defaults to no sound). Cancel here
        # means don't record at all.
        proceed, self.audio_device = audio.choose_source()
        if not proceed:
            return None
        # Do NOT show the control card yet: it grabs the keyboard (EXCLUSIVE
        # layer-shell), and showing it now would sit on top of the portal's
        # consent dialog and block window/monitor selection. The portal dialog
        # is the only UI until the stream is granted (see _on_ready).
        stype = SOURCE_WINDOW if self.target == "window" else SOURCE_MONITOR
        self._portal = ScreenCastPortal(stype)
        self._portal.start(self._on_ready, self._on_error)
        Gtk.main()
        return self.saved

    def _on_ready(self, fd, node_id, props):
        fraction = self._crop_fraction() if (self.target == "region" and self.region) else None
        print(f"[record] stream ready: fd={fd} node={node_id} props={dict(props)} "
              f"crop_fraction={fraction} audio={self.audio_device}", file=sys.stderr, flush=True)
        try:
            self.recorder = Recorder(self._tmp)
            self.recorder.build(fd, node_id, audio_device=self.audio_device)
            if fraction:
                self.recorder.set_crop_fraction(fraction)
            self.recorder.play()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return self._on_error(str(exc))
        self._started = True
        # Always register the recording so it can be stopped by signal — from
        # the tray's red ⏹ button OR from `cosmicshot record --stop` (a hotkey,
        # for setups without a tray). If a tray is running, light up its panel
        # Stop button too. Region/app-window also keep the floating card off the
        # recorded area; full-screen relies on the panel/hotkey (a card would be
        # in the shot) and only falls back to a card when there's no tray.
        from . import lock
        self._register_recording()
        have_tray = bool(lock.tray_pid())
        if have_tray:
            self._signal_tray()
        if self.target != "screen" or not have_tray:
            if self.target == "region" and self.region:
                self._dim = _RegionDim(self.region, self.monitors)
            self._build_control()
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)
        self._update()

    # -- stop-by-signal (tray button / `record --stop` hotkey) -----------
    def _register_recording(self):
        """Publish this recording's PID and listen for the stop signal."""
        import signal
        from . import lock
        self._panel_mode = True
        lock.write_recording_pid()
        self._sig_id = GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._on_stop_signal)
        print("[record] recording registered (signal-stoppable)",
              file=sys.stderr, flush=True)

    def _on_stop_signal(self):
        self.stop()  # _close_overlays() -> _exit_panel_mode() removes this source
        return True

    def _signal_tray(self):
        """Nudge the tray to re-read the recording state (icon + menu)."""
        import os
        import signal
        from . import lock
        pid = lock.tray_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGUSR1)
            except OSError:
                pass

    def _exit_panel_mode(self):
        if not self._panel_mode:
            return
        from . import lock
        if self._sig_id:
            GLib.source_remove(self._sig_id); self._sig_id = None
        lock.clear_recording_pid()
        self._signal_tray()
        self._panel_mode = False

    def _crop_fraction(self):
        """Region as (fx, fy, fw, fh) fractions of its host monitor — invariant
        under display scaling, so it maps correctly onto the device-pixel frame
        the encoder actually receives."""
        rx, ry, rw, rh = self.region
        cx, cy = rx + rw / 2, ry + rh / 2
        host = next((m for m in self.monitors
                     if m.x <= cx < m.x + m.width and m.y <= cy < m.y + m.height),
                    self.monitors[0] if self.monitors else None)
        if host is None or host.width <= 0 or host.height <= 0:
            return None
        return ((rx - host.x) / host.width, (ry - host.y) / host.height,
                rw / host.width, rh / host.height)

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
        self._exit_panel_mode()

    def stop(self):
        import os
        print("[record] Stop clicked", file=sys.stderr, flush=True)
        self._close_overlays()
        if self.recorder is None:
            self.error = "Recording never started — no window or screen was selected."
            return self._quit()
        rec_err = getattr(self.recorder, "error", None)
        self.recorder.stop()
        size = os.path.getsize(self._tmp) if os.path.exists(self._tmp) else -1
        print(f"[record] after stop: tmp={self._tmp} exists={size >= 0} size={size} "
              f"rec_err={rec_err}", file=sys.stderr, flush=True)
        # A header-only file (no frames captured) is a few hundred bytes; treat
        # anything that small as an empty recording rather than show a broken
        # preview.
        if not os.path.exists(self._tmp) or os.path.getsize(self._tmp) < 1024:
            self.error = (f"Recording produced no video ({rec_err})" if rec_err
                          else "Recording produced no video (the capture stream sent no frames).")
            try:
                if os.path.exists(self._tmp):
                    os.unlink(self._tmp)
            except OSError:
                pass
            return self._quit()
        # Preview: let the user watch it, then Save (asking where) or Discard.
        from . import config
        cfg = config.load()
        start_dir = cfg.get("video_save_dir") or self.save_dir
        suggested = os.path.basename(self.final_path)
        try:
            prev = PreviewWindow(self._tmp, self._save_temp, self._discard_temp,
                                 suggested_name=suggested, start_dir=start_dir)
            # While the preview waits to be saved this process still holds the
            # capture lock, so a new capture attempt should raise this window
            # rather than do nothing — register for the standard present signal.
            import signal
            from . import lock
            lock.write_active_pid()
            self._present_sig = GLib.unix_signal_add(
                GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                lambda: (prev.present(), True)[1])
            prev.run()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            # Don't lose the recording if the player fails to build — move it to
            # its final path and report it as saved.
            try:
                os.replace(self._tmp, self.final_path)
                self.saved = self.final_path
            except OSError:
                self.saved = self._tmp
            self.error = f"Saved without preview (player error: {exc})"
            self._quit()

    def _save_temp(self, dest):
        import os
        import shutil
        try:
            os.replace(self._tmp, dest)        # fast path: same filesystem
        except OSError:
            try:
                shutil.move(self._tmp, dest)   # cross-filesystem (different drive)
            except OSError:
                self.saved = self._tmp
                self.error = f"Could not save to {dest}"
                return self._quit()
        self.saved = dest
        # Remember the folder for next time.
        try:
            from . import config
            cfg = config.load()
            cfg["video_save_dir"] = os.path.dirname(os.path.abspath(dest))
            config.save(cfg)
        except Exception:
            pass
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
        from . import lock
        if self._present_sig:
            GLib.source_remove(self._present_sig); self._present_sig = None
        lock.clear_active_pid()
        GLib.idle_add(Gtk.main_quit)
