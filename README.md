# CosmicShot

A **CleanShot X-style** screenshot, **screen-recording** + annotation tool for
**Pop!_OS / COSMIC on Wayland**.

The stock COSMIC screenshot tool captures fine but can't annotate on the spot.
CosmicShot fills that gap: drag-select a region over a dimmed desktop, land
straight in an editor to draw arrows, boxes, text, blur sensitive bits, add
numbered steps — and **copy, save, or pin**. It also records video, takes
scrolling screenshots, and lives in your panel as a tray icon.

![CosmicShot in action](docs/cosmicshot-screenshot.png)

## Features

- **Dimmed region selector** — drag to select with a live `W × H` readout,
  crosshair, and resize handles. Multi-monitor aware. `Esc` cancels.
- **Capture modes** — **region**, a whole **screen**, or a specific **app window**.
- **Scrolling screenshots** — capture a long region or window and scroll;
  CosmicShot stitches the frames into one tall image.
- **Screen recording** — record a **region**, **app window**, or **whole screen**
  to MP4 (H.264) via the ScreenCast portal + GStreamer.
  - Optional audio (off by default): a quick dialog lets you pick **no sound**,
    **system sound (PC output)**, or a **microphone**.
  - A **red ● recording control** lets you Stop/Cancel; for full-screen the Stop
    button moves to the panel (a red ⏹) so nothing of CosmicShot is in the
    recording. You can also stop from a hotkey bound to `cosmicshot record --stop`.
  - After recording, a **preview player** lets you watch it, then **Save As…**
    (remembers the last folder) or **Discard**.
- **Instant annotation editor** with tools:
  - **Direct manipulation with any tool** — hover a shape (it highlights), drag
    its body to move or a handle to resize; arrows/lines have endpoint handles,
    boxes have 8. New shapes are auto-selected. `Delete` removes the selection;
    changing the colour re-colours it. The **Select** tool (`V`) rearranges only.
  - Arrow, Rectangle, Ellipse, Line
  - Freehand Pen, Highlighter (marker)
  - **Text boxes** — type in place; **click a text to re-edit** it. The box
    auto-grows and wraps when you drag a handle to set a width (resizing changes
    the **box width, never the font**). Left / centre / right / justify.
  - **Blur / pixelate** for redacting sensitive info — adjustable strength
  - **Spotlight / focus** — darkens everything outside a resizable box
  - **Numbered step counters** (auto-incrementing)
  - **Crop** — drag, then **Apply crop** (or `Enter`); keep annotating cropped.
  - Context-aware style control: Thickness / Font size / Blur / Darkness.
- **Undo / redo** (full history, including crop, move, and resize).
- **Close confirmation** — closing with unsaved edits asks Save / Discard / Cancel.
- **Cloud upload** — one click uploads and copies a shareable URL to your
  clipboard (default host: catbox.moe — free, no account, permanent). `Ctrl+U`.
- **Copy to clipboard**, **Save PNG**, or **Pin to screen** (floating,
  always-on-top; scroll to resize, drag to move, `Esc`/double-click to dismiss).
- **Settings** — version, one-click updates, and global keyboard shortcuts.
- **Panel tray icon** with a capture/record menu, auto-started at login.

## Install

CosmicShot relies on `cosmic-screenshot` (ships with the COSMIC desktop) for the
screen grab. Screen recording additionally uses PipeWire + GStreamer (see below).

### Recommended — `.deb` package (Pop!_OS / Ubuntu)

Download the latest `cosmicshot_*.deb` from the
[**Releases page**](https://github.com/davidboulay/CosmicShot/releases/latest), then:

```bash
sudo apt install ./cosmicshot_1.1.0_all.deb
```

`apt` pulls in the dependencies automatically. This installs the `cosmicshot`
command, a desktop entry, icons, and a login autostart for the panel tray icon.
Launch **CosmicShot** from the app grid or bind a hotkey (see Settings).

Remove it with `sudo apt remove cosmicshot`.

### Alternative — per-user script (no root)

```bash
sudo apt install python3-gi python3-gi-cairo python3-pil \
                 gir1.2-gtklayershell-0.1 gir1.2-ayatanaappindicator3-0.1 wl-clipboard
./install.sh
```

Copies the app to `~/.local/share/cosmicshot`, a launcher to `~/.local/bin`, a
desktop entry, and a tray autostart. Uses your system Python packages.

### Screen-recording dependencies

For the **Record** features, also install:

```bash
sudo apt install python3-gst-1.0 gstreamer1.0-pipewire \
                 gstreamer1.0-plugins-good gstreamer1.0-vaapi gstreamer1.0-plugins-bad
```

(`gstreamer1.0-libav` provides the AAC encoder used when you record audio.)

## Usage

```bash
cosmicshot                       # region capture (default) → edit
cosmicshot region                # same
cosmicshot screen                # pick a whole screen → edit  (alias: full)
cosmicshot window                # pick an app window → edit
cosmicshot scroll --target region   # scrolling screenshot of a region
cosmicshot scroll --target window   # scrolling screenshot of an app window
cosmicshot record --target region   # record a region to MP4
cosmicshot record --target window   # record an app window
cosmicshot record --target screen   # record a whole screen
cosmicshot record --stop            # stop the recording in progress (bind a hotkey)
cosmicshot open --file shot.png      # edit an existing image
cosmicshot settings                  # version / updates / shortcuts
cosmicshot tray                      # run the panel tray icon
```

### Tray icon (CleanShot-style menu)

The installer starts the tray automatically at login. It adds an icon to the
COSMIC panel with a capture/record menu, plus **Settings…**. While a full-screen
recording is running the icon turns into a red ⏹ **Stop recording** button.

> Needs `gir1.2-ayatanaappindicator3-0.1` (present on most COSMIC installs; the
> `.deb` recommends it).

### Settings

Open **Settings…** from the tray (or `cosmicshot settings`):

- **Version & updates** — see the installed version, **Check for updates**, and
  **Update now** (downloads the latest `.deb` and installs it via `pkexec`).
  Tick **Automatically check for updates** to be notified when a new release
  lands.
- **Global keyboard shortcuts** — assign a key combination per capture action.
  These are written into COSMIC's custom-shortcuts config so they work
  system-wide. Empty by default (a re-login guarantees COSMIC picks them up).

### Editor keys

| Key | Action | Key | Action |
|-----|--------|-----|--------|
| `V` | Select / move / resize | `T` | Text |
| `A` | Arrow | `N` | Step number |
| `R` | Rectangle | `B` | Blur |
| `E` | Ellipse | `X` | Crop |
| `L` | Line | `Delete` | Delete selected shape |
| `P` | Pen | `O` | Spotlight / focus |
| `H` | Highlighter | `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / Redo |
| `Ctrl+C` | Copy | `Ctrl+S` | Save |
| `Ctrl+U` | Upload & copy URL | `Enter` | Apply pending crop |
| `Esc` | Cancel / close (confirms if unsaved) | | |

## Configuration

Edit `~/.config/cosmicshot/config.json` (created on first run). Notable keys:

```jsonc
{
  "save_dir": "~/Pictures/Screenshots",
  "filename_pattern": "CosmicShot_%Y-%m-%d_%H-%M-%S.png",
  "default_color": "#ff3b30",
  "default_width": 4,
  "palette": ["#ff3b30", "#ff9500", "...", "#ffffff"],
  "pixelate_block": 12,        // default blur tool strength
  "spotlight_darkness": 0.6,   // 0..0.95
  "auto_copy_on_capture": false,
  "copy_on_save": true,        // also copy when saving / pinning
  "auto_update": false,        // check GitHub for updates on launch + periodically
  "video_save_dir": null,      // last folder used to save a recording

  // Cloud upload (Upload button / Ctrl+U). Default: catbox.moe (permanent).
  "upload_service": "https://catbox.moe/user/api.php",
  "upload_field": "fileToUpload",
  "upload_extra": { "reqtype": "fileupload" }
}
```

> **Uploads are public.** Anyone with the URL can view the image and nothing is
> encrypted — use the blur/spotlight tools to redact before uploading.

## How it works

Wayland forbids apps from reading the framebuffer directly, so CosmicShot grabs
the desktop via `cosmic-screenshot` (the COSMIC screenshot portal) for stills,
and via the **ScreenCast portal → PipeWire → GStreamer** for video. It then
renders **its own** overlay/editor on top with `gtk-layer-shell`; rendering is
cairo. Region recordings record the monitor and crop to the rectangle.

```
cosmicshot/
  app.py        orchestration + CLI
  capture.py    cosmic-screenshot wrapper + monitor geometry
  overlay.py    dimmed region selector + screen/window pickers + scroll capture
  scroll.py     scrolling-screenshot frame stitcher
  record.py     ScreenCast-portal recording (pipeline, control, preview)
  audio.py      audio-source discovery + picker
  windows.py    per-window geometry (COSMIC toplevel-info protocol)
  editor.py     annotation editor window (canvas, toolbar, undo/redo)
  tools.py      annotation primitives (arrow, rect, text, blur, …)
  imaging.py    PIL ↔ cairo, pixelate/blur source
  export.py     render → clipboard / disk / png bytes
  pin.py        floating always-on-top pinned screenshot
  tray.py       panel tray icon + recording Stop + update checks
  settings.py   settings window (version, updates, shortcuts)
  updates.py    GitHub release check + .deb install via pkexec
  shortcuts.py  writes CosmicShot shortcuts into COSMIC's config
  config.py     settings
```

## Troubleshooting

- **Nothing happens / "no file"** — ensure `cosmic-screenshot` works:
  `cosmic-screenshot --interactive=false --save-dir /tmp`.
- **Recording produces no video** — install the GStreamer packages above and
  check an H.264 encoder is present (`gst-inspect-1.0 vah264enc`).
- **Overlay doesn't appear** — check `gir1.2-gtklayershell-0.1` is installed.
- **Copy does nothing** — install `wl-clipboard`; verify with `wl-paste --list-types`.
- **No tray icon** — install `gir1.2-ayatanaappindicator3-0.1`, then run `cosmicshot tray`.
- **`cosmicshot: command not found`** (script install) — add `~/.local/bin` to your `PATH`.

## License

MIT.
