# PrintPal

A native Windows desktop studio for the **YHK-0A73 / CCHW-MAC** mini thermal printer.
Design text, dividers, QR codes and images on a 384-dot canvas, preview the exact
1-bit output, and print straight from the PC over Bluetooth LE — no phone needed.

![paper width: 58 mm · 384 dots · 203 dpi](https://img.shields.io/badge/paper-58%20mm%20%C2%B7%20384%20dots%20%C2%B7%20203%20dpi-f0a43c)

## What it does

- **Text blocks** — any installed Windows font, size, bold/italic, letter and line
  spacing, alignment, and automatic wrapping to the paper width.
- **Images** — PNG/JPEG/WebP/GIF, drag-and-drop, fit modes (width, contain, cover,
  actual), rotation, border, and per-image dithering (Floyd–Steinberg, Atkinson,
  ordered Bayer 4×4/8×8, or hard threshold).
- **QR codes, dividers and spacers** — print-ready at native resolution.
- **Label maker** — type a list and get one auto-fitted sticker per line, or build a
  shelf tag with an oversized left-aligned name, a small bold caps footnote and an
  optional Code 128 barcode along the bottom edge.
- **Accurate preview** — the preview is the same 1-bit bitmap that is sent to the
  print head, with paper height in millimetres.
- **Real hardware integration** — battery level, firmware, serial number and model
  queried from the printer; paper feed, test page, copies and job progress.
- **Projects** — save/open `.json` projects, export the preview as PNG, templates for
  notes, labels, checklists and receipts.

## Hardware notes

| Item | Value |
| --- | --- |
| Printer | YHK-0A73 / CCHW-MAC |
| BLE address (this unit) | `24:00:38:00:0A:73` |
| BLE service | `49535343-fe7d-4ae5-8fa9-9fafd205e455` (ISSC UART) |
| Write characteristic | `49535343-8841-43f4-a8d4-ecbe34729bb3` (write without response) |
| Notify characteristic | `49535343-1e4d-4bd9-ba61-23c647249616` |
| Print head | 384 dots, 203 dpi, 58 mm paper |
| Battery | 2-cell pack, reports ~6400–8400 mV |
| Serial / model | `CCHW240038000A73` / `0212` |

The printer speaks an ESC/POS raster dialect rather than plain text:

1. `ESC @` (`1B 40`) initialises the printer.
2. `1D 49 F0 19` is the "start print" magic used by the official app.
3. Each row is sent as `GS v 0` (`1D 76 30 00 <width bytes> <height>`) followed by
   inverted bitmap data — `1` bits burn, and **no line feed is sent between rows**.
4. `0A` × *n* feeds the paper at the end.

A second protocol (AE30 cat-printer, service `0000ae30-…`) is implemented as a
fallback for other YHK firmware revisions; it is selected automatically.

## Requirements

- Windows 10/11 with a Bluetooth LE adapter.
- Python 3.10+ (developed against 3.12).
- The printer switched on and **not connected to the WalkPrint phone app** — BLE
  printers accept one central at a time.

## Install

```bat
cd PrintPal
py -3 -m pip install -r requirements.txt
```

If the printer is already paired in Windows, PrintPal can also use its outgoing COM
port (Serial mode in the Connect dialog), but Bluetooth LE is the primary and most
reliable route for this model.

## Install on another PC

Two prebuilt packages are produced under `dist/`:

- **`installer/PrintPal-Setup-1.0.0.exe`** — per-user installer. It needs no admin
  rights and installs to `%LOCALAPPDATA%\Programs\PrintPal`. It creates Start menu
  and desktop shortcuts, optionally starts PrintPal at sign-in, and registers an
  uninstaller in Windows Settings → Apps.
- **`PrintPal-Portable-1.0.0.zip`** — unzip anywhere and run `PrintPal.exe`.

Neither package needs Python or any other dependency on the target computer. The
app still keeps its settings in `%APPDATA%\PrintPal` and uploaded images in
`%LOCALAPPDATA%\PrintPal\images`, so upgrading or reinstalling preserves your work.

## Run

```bat
py -3 -m printpal
```

or double-click **`PrintPal.bat`**. The app opens in a dedicated Chrome/Edge window
(no browser tabs or extensions), starts a local server on `127.0.0.1` and
auto-connects to the last printer you used.

Useful flags:

```bat
py -3 -m printpal --server-only      REM serve only, open the URL yourself
py -3 -m printpal --port 8800        REM prefer a different local port
py -3 -m printpal --no-autoconnect   REM don't connect on launch
```

Close the console window (or press `Ctrl+C`) to stop the app.

## First print

1. Turn the printer on and make sure the phone app is disconnected.
2. Click **Connect**, pick `YHK-0A73` (it is listed first and marked as a printer),
   and wait for the battery/serial readout.
3. Adjust the document, then press **Print**. Use **Test page** to verify alignment
   and **Calibration** to pick readable text sizes and a scannable barcode.

## Labels

Switch the workspace to **Label maker** in the editor toolbar. Two label types are
available:

- **Label list** — type or paste one label per line. Blank lines are skipped, and
  each line is auto-fitted to the paper width up to the chosen maximum letter size.
  The whole list prints as one continuous strip, separated by the gap setting.
- **Shelf tag** — a large left-aligned item name (one line when it can stay at least
  half the maximum size, otherwise two lines), a smaller bold all-caps footnote, and
  an optional Code 128 barcode at the absolute bottom of the tag. Title and footnote
  sizes are adjustable; the defaults (64 and 22 dots) stay readable at 203 dpi. Keep
  the barcode data to 12 characters or fewer. Leave it empty to encode the footnote
  automatically. The barcode is drawn at print resolution after dithering, so its bars
  stay crisp instead of turning into gray noise.

The preview shows a **Heads up** strip when something needs attention, for example a
two-line title (the tag prints taller on roll paper), a long title that would print
small, or a barcode that is too dense/long to scan reliably. Those warnings also
travel in the `X-Warnings` response header so the preview and the printed bitmap
always agree.

Labels are saved with the project (`mode`, `labels`) and restored on launch.

## Print quality tips

- Press **Calibration** once and keep the strip. It prints a text-size ladder, a
  footnote ladder, edge/dot-gain patterns, and the same barcode at three heights, so
  you can choose sizes that survive the print head instead of guessing.
- **Text** is thresholded at 160 by default; raise the threshold for thin fonts on
  cheap paper, lower it if letters bleed together.
- **Photos** default to Floyd–Steinberg. Atkinson gives lighter, higher-contrast
  output; Bayer gives a visible halftone pattern that survives low print energy.
- **Dropped rows near the bottom of a page** mean the BLE link is overrunning. Raise
  `ble.chunk_delay_ms` (default `10`) in
  `%APPDATA%\PrintPal\settings.json`, or lower the writing speed.
- **Faint output** usually means a low battery — the pack sags under load. Charge it
  before long prints.

## Architecture

```
printpal/
  app.py         CLI, free-port selection, app-window launcher
  server.py      local HTTP API + static file server
  state.py       printer state, job runner, progress and cancellation
  transport.py   BLE (Bleak) and serial/RFCOMM sessions, scanning, status
  protocol.py    ESC/POS raster + cat-printer framing, status parsers
  render.py      document → 1-bit bitmap renderer, dithering, image store
  fonts.py       Windows font registry enumeration and resolution
  settings.py    JSON settings in %APPDATA%\PrintPal
web/             no-build-step HTML/CSS/JS interface
assets/          app icon used by the EXE and installer
installer/       Inno Setup script
run_printpal.py  windowed entry point used by PyInstaller
PrintPal.spec    PyInstaller build definition
build.ps1        rebuilds the EXE, portable zip and setup executable
tests/           unittest suite
```

The UI talks to the backend through a small JSON API (`/api/bootstrap`, `/api/scan`,
`/api/connect`, `/api/preview`, `/api/print`, `/api/state`, …). Nothing leaves the
machine; the server binds to loopback only.

## Build the packages

```bat
py -3 -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The build uses PyInstaller and Inno Setup 6. It rebuilds `dist/PrintPal/`, the
portable zip and the setup executable. Install Inno Setup 6 from
<https://jrsoftware.org/isinfo.php> before running the script if it is not already
on the machine.

### Build in the cloud

`.github/workflows/build-windows.yml` builds the packages on a GitHub-hosted
`windows-latest` runner, so nothing needs to be installed on your PC:

1. Push the project to a GitHub repository.
2. Run the **Build Windows packages** workflow manually from the Actions tab, or
   push a tag such as `v1.0.0`.
3. Download the setup and portable zip from the workflow run. Tag builds also
   publish them as a GitHub release.

The workflow installs Python, the build requirements and Inno Setup, runs the
unit tests, then calls `build.ps1`. PyInstaller must run on Windows to produce a
Windows executable; a Linux runner cannot build these packages.

Other Windows CI services such as Azure Pipelines, AppVeyor or a Windows VM work
the same way: install Python 3.12 and Inno Setup 6, install
`requirements-build.txt`, then run `build.ps1`.

## Tests

```bat
py -3 -m unittest discover -s tests -v
```

The suite covers raster packing and chunking, status/identity parsing, cat-printer
framing, document layout, wrapping, dithering, inverting, image blocks, QR codes,
Code 128 encoding, auto-fit label text and shelf tags with/without barcodes.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Printer not listed in Connect | Switch it on, move closer, close the phone app, then scan again. |
| Connect fails with "access denied" | Remove the printer in Windows Bluetooth settings, re-pair, retry. |
| `No printer connected` after launch | Auto-connect needs the printer awake; click Connect manually. |
| Bottom of page missing | Increase `ble.chunk_delay_ms` (try 14–20). |
| Barcode prints as broken bars | Keep barcode data to 12 characters or fewer, enable write-with-response in Advanced, and raise `ble.chunk_delay_ms`. |
| Text too large or footnote too small | Adjust **Title max size** and **Footnote size** in the Shelf tag panel. |
| Everything prints solid black | The paper is loaded with the thermal side facing the wrong way. |
| Port already in use | PrintPal picks the next free port automatically; check the console URL. |
