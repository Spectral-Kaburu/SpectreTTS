# SpectreTTS

A local, lightweight text-to-speech daemon for Kali Linux. Highlight any text,
hit a hotkey, and have it read aloud — no cloud calls, no API keys, no
GUI window to manage. Built on [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
a compact open-weights TTS model that runs comfortably on CPU.

## Why this exists

Sometimes you need to read something while your hands are busy. SpectreTTS
runs as a background daemon with the model held warm in memory, listens on
a local Unix socket, and is triggered by a single global hotkey
(`Ctrl+Alt+R`) that works regardless of which window has focus — even under
Wayland.

## Architecture

```
Highlight text → Ctrl+Alt+R → hotkey_trigger.py → /tmp/spectretts.sock → daemon (warm model) → audio out
```

The hotkey script is intentionally tiny — it never imports torch or touches
Kokoro. It just grabs the X11 PRIMARY selection and forwards it over a Unix
socket to the long-running daemon, which holds the model in RAM and starts
streaming audio back almost immediately.

```
spectretts/
├── engine/
│   ├── tts_engine.py          Core Kokoro wrapper — streaming synth + playback
│   ├── selection_grabber.py   Grabs highlighted text via xclip
│   └── socket_server.py       Unix socket command listener
├── tray/
│   ├── hotkey_trigger.py      Bound to Ctrl+Alt+R — sends selection to daemon
│   ├── register_hotkey.sh     One-time GNOME keybinding setup
│   ├── systray_app.py         GTK/AppIndicator tray icon + menu
│   └── daemon.py              Main entrypoint — starts engine + socket + tray
├── assets/                    Tray icons
├── config/                    Saved voice/speed preferences
└── requirements.txt
```

## Requirements

- Python 3.11 (Kokoro's dependency chain, via `misaki`/`spacy`, doesn't yet
  have prebuilt wheels for 3.13 — see [Notes](#notes-on-python-version) below)
- `espeak-ng` (phonemization backend)
- `xclip` (selection grabbing)
- GTK 3 + AyatanaAppIndicator (tray icon)

System packages:
```bash
sudo apt install -y espeak-ng libportaudio2 xclip xdotool \
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  libayatana-appindicator3-dev gir1.2-ayatanaappindicator3-0.1
```

Python packages (inside the venv):
```bash
pip install -r requirements.txt
```

## Setup

```bash
# 1. Activate the project venv (Python 3.11)
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Register the global hotkey (one-time, idempotent)
chmod +x tray/register_hotkey.sh tray/hotkey_trigger.py
./tray/register_hotkey.sh

# 4. Start the daemon (loads the model, opens the socket, shows tray icon)
python tray/daemon.py
```

Once running: highlight any text, anywhere, press `Ctrl+Alt+R`.

## Usage

| Action | How |
|---|---|
| Read selected text | Highlight text, `Ctrl+Alt+R` |
| Pause / resume | Tray icon menu |
| Stop | Tray icon menu |
| Change voice | Tray icon menu (54 voices, 8 languages) |
| Adjust speed | Tray icon menu (0.5x–2.0x) |

## Notes on Python version

Kali's default Python (3.13 at time of writing) doesn't yet have prebuilt
wheels for `spacy`/`thinc`/`blis`, which Kokoro pulls in transitively via
`misaki[en]`. Attempting to install on 3.13 triggers a source compile that
fails on Cython/NumPy ABI mismatches. Building Python 3.11 from source
(`./configure --prefix=$HOME/.python311 && make && make install`, **without**
`--enable-optimizations` to avoid the bootstrap profiling step crashing) and
creating the venv from that interpreter avoids the entire issue, since
prebuilt wheels exist for 3.11 across the board.

This Python 3.11 install lives outside the project (`~/.python311`) and the
venv references it — don't delete that folder. If you'd rather the venv be
fully standalone, recreate it with `python3.11 -m venv --copies .venv`,
which copies the interpreter binary into the venv instead of symlinking it.

## License

Kokoro-82M is Apache 2.0. This project's code is yours to do with as you like.