# Assistant (Voice + Text Desktop AI)

English-first desktop assistant with wake word, voice auth, password auth, text mode, file/app/system control, coding-project workflows, and local/cloud model fallbacks.

## What This Project Does

This assistant is built to operate your computer by intent, not rigid command templates.  
It supports:

- Wake-word voice interaction
- Secure startup + secure text-mode entry (password)
- Optional voice authentication
- Text mode (`Ctrl+Space`) with the same command capability as voice mode
- App/file/folder/system setting control
- Project pinning (`@/path`) and codebase-aware coding operations
- Conversation history management
- Undo/redo for reversible actions
- Dynamic terminal wave UI (can be turned on/off)
- Dynamic terminal visual themes (`waves` and `bubble`) with text-only fallback
- Cloud + local fallback for LLM, STT, and TTS
- English-only runtime language (intent-first parsing, not rigid templates)
- Confirmation prompts accept keyboard or voice responses

## Core Pipeline

1. Wake-word listener (Vosk) waits for assistant name variants.
2. Optional speaker verification checks user voice sample.
3. Query recording + transcription (Deepgram online, local Whisper fallback).
4. Intent engine resolves command vs conversation.
5. System action executor runs app/file/device operations.
6. LLM handles non-system conversation and coding generation.
7. TTS speaks response (Edge-TTS -> Piper -> pyttsx3 fallback).

## Platform Support

- Windows: Full support (primary target).
- Linux: Supported for core assistant behavior and major system controls.

Linux support includes:
- app/file/folder commands
- volume/mic (via `pactl`/`amixer`)
- brightness (via `brightnessctl`/`xbacklight`)
- wifi/bluetooth/airplane mode (via `nmcli`/`rfkill`)
- energy saver (via `powerprofilesctl`)
- night light and some settings (via `gsettings` / GNOME tools when available)
- process listing/termination and terminal command execution

Linux command coverage from cheat-sheet style requests is handled in two ways:
- Direct command execution (`run terminal command ...`, `execute command ...`, or writing command syntax directly)
- Natural-language mapping for common command intents (date/time, calendar, uptime, kernel info, CPU/memory info, disk usage, routing/network interfaces, listening ports, reverse lookup, process tree, manual lookup, downloads)

## Prerequisites

## Python

- Python `3.10+`
- `pip`
- Virtual environment support (`venv`)

## System Packages (Linux)

Install these first (Ubuntu/Debian example):

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-dev \
  build-essential portaudio19-dev ffmpeg \
  libasound2-dev libpulse-dev \
  espeak-ng flac \
  network-manager rfkill bluez \
  power-profiles-daemon brightnessctl \
  gnome-control-center wmctrl
```

Notes:
- `gnome-control-center`, `gsettings`, `wmctrl` are optional but improve settings/app-window control.
- If your distro is not Debian-based, install equivalent packages with your package manager.

## Windows Requirements

- Windows 10/11
- PowerShell available
- Python 3.10+ installed and added to PATH

## Installation

1. Clone repo:

```bash
git clone <your-repo-url>
cd assistant
```

2. Create and activate virtualenv:

Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install Python dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

4. Download Vosk wake-word model:

- Place model at:
  - `models/vosk/vosk-model-small-en-us-0.15`
- This path is used by default by `assistant.main`.

## Environment Variables / API Keys

Create a `.env` (or export in shell) for optional cloud features.

## LLM (Conversation + Coding)

- `GROQ_API_KEY` (optional cloud primary)
- `GROQ_MODEL` (default: `llama-3.1-8b-instant`)
- `GROQ_CODE_MODEL` (optional coding model override)
- `OPENROUTER_API_KEY` (optional cloud secondary)
- `OPENROUTER_MODEL`
- `OPENROUTER_CODE_MODEL`
- `OPENROUTER_URL` (default OpenRouter chat endpoint)
- `OLLAMA_URL` (local fallback, default: `http://localhost:11434/api/generate`)
- `OLLAMA_MODEL` (default: `llama3.1:latest`)
- `OLLAMA_CODE_MODEL` (coding fallback model)

## STT

- `DEEPGRAM_API_KEY` (optional online STT primary)
- `DEEPGRAM_MODEL` (default: `nova-2`)
- `WHISPER_MODEL_SIZE` (local fallback model, default `small`)
- `WHISPER_MODEL_PATH` (optional custom local model path)

## TTS

- `EDGE_TTS_VOICE`, `EDGE_TTS_RATE`, `EDGE_TTS_PITCH`, `EDGE_TTS_VOLUME` (optional overrides)
- `PIPER_MODEL_PATH` (optional local neural TTS model path)

## Example: Set env vars

Windows PowerShell:

```powershell
$env:GROQ_API_KEY="your_key_here"
$env:DEEPGRAM_API_KEY="your_key_here"
$env:OLLAMA_URL="http://localhost:11434/api/generate"
```

Linux:

```bash
export GROQ_API_KEY="your_key_here"
export DEEPGRAM_API_KEY="your_key_here"
export OLLAMA_URL="http://localhost:11434/api/generate"
```

## Run

From project root:

```bash
python -m assistant.main
```

## First-Time Setup

On first run, assistant will guide through:

1. assistant name
2. wake response preference
3. interface mode selection (`waves`, `bubble`, or `text`)
4. voice preset selection + preview
5. password setup
6. optional voice authentication sample
7. autostart setup

Every setup choice supports keyboard input and voice fallback:
- You can type answers directly.
- If you leave the prompt empty, assistant listens for your spoken answer.
- Confirmation prompts accept both typed and spoken `yes/no`.

## Key Shortcuts

- `Ctrl+Space`: toggle text mode
- `Ctrl+Shift+R`: factory reset + rerun setup
- `Ctrl+Shift+Alt`: start new conversation
- `Shift+Enter`: stop current generation/TTS

## Command Coverage (Examples)

## Modes

- `open text mode`
- `enable voice mode` (inside text mode for spoken responses)

## Wave UI

- `enable waves`
- `disable waves`
- `turn on waves`
- `turn off waves`
- `enable bubble`
- `disable bubble`
- `turn on bubble interface`
- `turn off bubble interface`

When visual mode is disabled, assistant uses classic text status output.
During setup/reset and any terminal text-heavy output, visual rendering is cleared first to prevent ASCII overlap.

## Apps / System

- `open vlc`
- `close camera`
- `close all apps`
- `shutdown device`
- `restart my computer`
- `list all the background processes`
- `kill process chrome`
- `terminate process 1234`

## Settings

- `set wake word sensitivity to 70 percent`
- `set sound to 40 percent`
- `set brightness to 60 percent`
- `turn wifi off`
- `turn bluetooth on`
- `enable airplane mode`
- `set energy saver on`
- `turn night light off`

Status queries:

- `sound level status`
- `current brightness level`
- `microphone status`
- `wake word sensitivity status`
- `wake response status`
- `voice model status`

## Files / Folders

- `create file notes.txt in downloads`
- `delete words dot txt`
- `list contents of documents folder`
- `move report.pdf from downloads to documents`

## Web / Media

- `open youtube`
- `open github slash your-username`
- `play despacito on youtube`
- `play lo-fi beats on spotify`

## Coding / Project Mode

- `@/path/to/project fix auth bug in login flow`
- `@/new_project create a FastAPI project with auth and sqlite`

## Linux Command Coverage

For direct Linux command execution (including commands from Linux cheat sheets), use:

- `run terminal command <your command>`
- `execute command <your command>`

Examples:

- `run terminal command ls -la`
- `execute command sudo apt update`
- `run terminal command journalctl -f`

Natural-language Linux examples:

- `show current date and time` -> `date`
- `show this month calendar` -> `cal`
- `show kernel information` -> `uname -a`
- `show cpu information` -> `cat /proc/cpuinfo`
- `show memory information` -> `cat /proc/meminfo`
- `show disk usage` -> `df -h`
- `show directory space usage` -> `du -h`
- `show network interfaces` -> `ip addr show`
- `show routing table` -> `ip route show`
- `show listening ports` -> `ss -tuln`
- `reverse lookup 8.8.8.8` -> `dig -x 8.8.8.8`

This command path returns terminal output directly in the assistant response.

## Autostart

- Windows: startup registry/launcher handled by assistant setup.
- Linux: use `assistant/serve/linux.service` as template (edit user/path first), then:

```bash
sudo cp assistant/serve/linux.service /etc/systemd/system/assistant.service
sudo systemctl daemon-reload
sudo systemctl enable assistant.service
sudo systemctl start assistant.service
```

## Troubleshooting

## No wake-word detection

- Verify Vosk model path exists:
  - `models/vosk/vosk-model-small-en-us-0.15`
- Increase sensitivity:
  - `set wake word sensitivity to 80 percent`

## Audio control fails on Linux

- Install/check: `pactl` or `amixer`
- For brightness: install/check `brightnessctl` or `xbacklight`

## Bluetooth/Wi-Fi control fails

- Ensure `nmcli` / `rfkill` are installed
- Some operations may require elevated permissions

## TTS fallback issues

- Edge-TTS needs network
- Piper needs a valid `PIPER_MODEL_PATH`
- pyttsx3 fallback depends on system voice packages

## STT fallback issues

- If Deepgram not configured, ensure local Whisper model can load
- Keep microphone device accessible (OS permissions)

## Security Notes

- Assistant executes system-level actions; run only on trusted machines.
- Use a strong password.
- Voice auth strictness can be tuned (`0` to `100`).

## License

Add your license details here.
