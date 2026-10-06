# SpectreTTS

A local, lightweight text-to-speech daemon for Linux. Highlight any text,
hit a hotkey, and have it read aloud — no cloud calls, no API keys, no
GUI window to manage. Features two excellent local TTS engines: **Piper TTS** 
(extremely fast, CPU-friendly) and **Kyutai Pocket-TTS** (high-quality streaming).

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

The hotkey script is intentionally tiny — it never imports heavy ML libraries. 
It just grabs the X11 PRIMARY selection and forwards it over a Unix socket to 
the long-running daemon, which holds the model in RAM and starts streaming audio back almost immediately.

```
spectretts/
├── engine/
│   ├── tts_engine.py          Core TTS wrapper — streaming synth + playback
│   ├── backends/              Pluggable backend engines (Piper, Pocket)
│   ├── selection_grabber.py   Grabs highlighted text via xclip
│   └── socket_server.py       Unix socket command listener
├── tray/
│   ├── hotkey_trigger.py      Bound to Ctrl+Alt+R — sends selection to daemon
│   ├── register_hotkey.sh     One-time GNOME keybinding setup
│   ├── install_service.sh     Installs background systemd service
│   ├── systray_app.py         GTK/AppIndicator tray icon + menu
│   └── daemon.py              Main entrypoint — starts engine + socket + tray
├── assets/                    Tray icons
└── setup.sh                   Automated full-setup script
```

## Setup (Automated)

The easiest way to get everything running is to use the provided setup script. 
It installs all dependencies, creates a virtual environment, installs the systemd service, 
and sets up your global GNOME hotkey.

```bash
cd SpectreTTS
./setup.sh
```

## Setup (Manual)

If you prefer to set it up step-by-step:

1. **System packages:**
   ```bash
   sudo apt install -y espeak-ng libportaudio2 xclip xdotool \
     python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
     libayatana-appindicator3-dev gir1.2-ayatanaappindicator3-0.1
   ```
2. **Virtual Environment & Dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configuration:**
   Copy the default `.env` from the setup script, or run `./setup.sh` to generate it. You can switch between `piper` and `pocket` backend here.
4. **Register the global hotkey (GNOME):**
   ```bash
   chmod +x tray/register_hotkey.sh tray/hotkey_trigger.py
   ./tray/register_hotkey.sh
   ```
5. **Start the daemon via systemd (recommended):**
   ```bash
   chmod +x tray/install_service.sh
   ./tray/install_service.sh
   ```
   *Or just run `python tray/daemon.py` directly to test it.*

## Usage

Once running: highlight any text, anywhere, press `Ctrl+Alt+R`.

| Action | How |
|---|---|
| Read selected text | Highlight text, `Ctrl+Alt+R` |
| Pause / resume | Tray icon menu |
| Stop | Tray icon menu |
| Change voice | Tray icon menu |
| Adjust speed | Tray icon menu (Note: Piper supports speed changes, Pocket currently does not) |
| Reader Window | Tray icon menu -> Toggle Reader Window for a karaoke-style view |

## Configuration & Voices

Models are downloaded automatically from HuggingFace on first use.
You can configure the active TTS backend by editing the `.env` file in the project root:

```ini
SPECTRETTS_BACKEND=piper
SPECTRETTS_PIPER_VOICE=en_US-lessac-medium
```

## License

This project's code is provided as-is. Piper TTS and Pocket-TTS models and engines are governed by their respective licenses (typically MIT or Apache 2.0).