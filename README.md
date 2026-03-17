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
- One-command installer for dependencies + local models
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
7. If response is unknown/insufficient, assistant performs web snippet fallback and summarizes results.
8. TTS speaks response (Edge-TTS -> Piper -> pyttsx3 fallback).

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

3. Run the one-shot installer (recommended):

```bash
python -m assistant.installer
```

If installed as a package with console scripts, you can also run:

```bash
assistant-install
```

This single command performs:
- Python dependency installation from `requirements.txt`
- Vosk wake model download/extract to `models/vosk/vosk-model-small-en-us-0.15`
- Local Whisper model preload (`small` by default)
- Ollama auto-install attempt (Windows: `winget`, Linux: install script)
- Ollama model pulls (`OLLAMA_MODEL` and `OLLAMA_CODE_MODEL`)

Optional flags:

```bash
python -m assistant.installer --skip-ollama --skip-whisper --skip-vosk
python -m assistant.installer --chat-model llama3.1:latest --code-model qwen2.5-coder:7b --whisper-model small
```

4. (Optional manual path) if you do not use the installer:

- Install dependencies: `pip install -r requirements.txt`
- Download Vosk model to `models/vosk/vosk-model-small-en-us-0.15`
- Preload Whisper model and pull Ollama models manually (see below)

## Model + API Setup (Beginner Guide)

This assistant uses both local and cloud models with fallback chains.

Important:
- This project does not auto-load `.env` by itself.
- You must set environment variables in your shell or OS environment.

## 1. Install local models/assets first

If you ran `python -m assistant.installer`, this section is mostly already done.

1. Wake-word model (required):
   - Download `vosk-model-small-en-us-0.15`.
   - Place it at `models/vosk/vosk-model-small-en-us-0.15`.

2. Local STT fallback model (recommended):
   - Engine: `faster-whisper`.
   - Default local model name: `small`.
   - By default, local model auto-download is disabled (`WHISPER_ALLOW_DOWNLOAD=False`), so pre-download once:

```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', local_files_only=False)"
```

3. Local LLM fallback via Ollama (strongly recommended):
   - Install Ollama: `https://ollama.com/download`
   - Pull at least one chat model and one coding model:

```bash
ollama pull llama3.1:latest
ollama pull qwen2.5-coder:7b
```

4. Optional local neural TTS model (Piper):
   - Package is already in requirements (`piper-tts`), but you still need a voice model file.
   - Download a Piper voice model from the Piper voices release pages and set `PIPER_MODEL_PATH` to that `.onnx` file.
   - If Piper is not configured, TTS falls back to `pyttsx3`.

## 2. Create cloud API keys (optional, but improves quality/speed)

1. Groq:
   - Create account and key in the Groq Console (`https://console.groq.com/keys`).
   - Copy your API key.

2. OpenRouter:
   - Create account and key in OpenRouter (`https://openrouter.ai/keys`).
   - Add credits/quota if required by your selected model.
   - Copy your API key.

3. Deepgram:
   - Create account and key in Deepgram Console (`https://console.deepgram.com/`).
   - Copy your API key.

## 3. Set API keys in your environment

Windows PowerShell (current terminal session only):

```powershell
$env:GROQ_API_KEY="your_groq_key"
$env:OPENROUTER_API_KEY="your_openrouter_key"
$env:DEEPGRAM_API_KEY="your_deepgram_key"
$env:GROQ_MODEL="llama-3.1-8b-instant"
$env:GROQ_CODE_MODEL="llama-3.1-70b-versatile"
$env:OPENROUTER_MODEL="meta-llama/llama-3.3-8b-instruct:free"
$env:OPENROUTER_CODE_MODEL="qwen/qwen-2.5-coder-32b-instruct"
$env:OLLAMA_MODEL="llama3.1:latest"
$env:OLLAMA_CODE_MODEL="qwen2.5-coder:7b"
```

Windows persistent (new terminals after restart/login):

```powershell
setx GROQ_API_KEY "your_groq_key"
setx OPENROUTER_API_KEY "your_openrouter_key"
setx DEEPGRAM_API_KEY "your_deepgram_key"
setx OLLAMA_MODEL "llama3.1:latest"
setx OLLAMA_CODE_MODEL "qwen2.5-coder:7b"
```

Linux/macOS (current shell session):

```bash
export GROQ_API_KEY="your_groq_key"
export OPENROUTER_API_KEY="your_openrouter_key"
export DEEPGRAM_API_KEY="your_deepgram_key"
export GROQ_MODEL="llama-3.1-8b-instant"
export GROQ_CODE_MODEL="llama-3.1-70b-versatile"
export OPENROUTER_MODEL="meta-llama/llama-3.3-8b-instruct:free"
export OPENROUTER_CODE_MODEL="qwen/qwen-2.5-coder-32b-instruct"
export OLLAMA_MODEL="llama3.1:latest"
export OLLAMA_CODE_MODEL="qwen2.5-coder:7b"
```

Linux/macOS persistent:
- Add the same `export ...` lines to `~/.bashrc` or `~/.zshrc`, then run `source ~/.bashrc` (or reopen terminal).

## 4. Environment variables reference

LLM:
- `GROQ_API_KEY`
- `GROQ_MODEL` (default: `llama-3.1-8b-instant`)
- `GROQ_CODE_MODEL` (coding-specific cloud model)
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `OPENROUTER_CODE_MODEL`
- `OPENROUTER_URL` (default: `https://openrouter.ai/api/v1/chat/completions`)
- `OLLAMA_URL` (default: `http://localhost:11434/api/generate`)
- `OLLAMA_MODEL` (local general fallback)
- `OLLAMA_CODE_MODEL` (local coding fallback)

STT:
- `DEEPGRAM_API_KEY`
- `DEEPGRAM_MODEL` (default: `nova-2`)
- `WHISPER_MODEL_SIZE` (default: `small`)
- `WHISPER_MODEL_PATH` (optional custom path)
- `WHISPER_ALLOW_DOWNLOAD` (default: false; set true only if you want runtime model download)

TTS:
- `EDGE_TTS_VOICE`, `EDGE_TTS_RATE`, `EDGE_TTS_PITCH`, `EDGE_TTS_VOLUME`
- `PIPER_MODEL_PATH`
- `OPENVOICE_INFER_ARGS` (optional extra args for OpenVoice CLI)
- `OPENVOICE_DEVICE` (`cpu` or `cuda`, default: `cpu`)

## 5. Provider fallback order used by this project

- LLM (general chat): `Groq -> OpenRouter -> Ollama`
- LLM (coding requests): `GROQ_CODE_MODEL -> OPENROUTER_CODE_MODEL -> OLLAMA_CODE_MODEL`
- STT: `Deepgram -> local Faster-Whisper`
- TTS: `Edge-TTS -> Piper -> pyttsx3`

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

## Interface UI

- `enable waves`
- `disable waves`
- `turn on waves`
- `turn off waves`
- `enable bubble`
- `disable bubble`
- `turn on bubble interface`
- `turn off bubble interface`
- `switch to text interface`

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
- `search for youtube entropy video`
- `search for github python websocket examples`
- `open youtube in incognito mode`
- `open wikipedia in new tab`

## News

- `what is new today`
- `what are the headlines`
- `what's trending today`
- `what's trending today in AI`

Browser behavior:
- If a browser is already open and no `new tab/new window/incognito` is requested, assistant tries to reuse the existing tab (address-bar navigation) instead of opening extra windows.
- If user explicitly asks for `incognito`, `new tab`, or `new window`, assistant opens accordingly.
- YouTube/YT Music commands now open search-results pages by intent query instead of forcing direct watch links.

## Coding / Project Mode

- `@/path/to/project fix auth bug in login flow`
- `@/new_project create a FastAPI project with auth and sqlite`

### Does `@/project` switch to a coding model?

Yes.

When the action resolves to `project_code`, code generation paths use the coding-model method (`generate_code`) instead of general chat generation.

Actual routing:
- Cloud coding primary: `GROQ_CODE_MODEL`
- Cloud coding secondary: `OPENROUTER_CODE_MODEL`
- Local coding fallback: `OLLAMA_CODE_MODEL`

If no coding model env vars are set, each provider falls back to its general model value.

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
- Custom voices require XTTS (Coqui) or OpenVoice configuration

## STT fallback issues

- If Deepgram not configured, ensure local Whisper model can load
- Keep microphone device accessible (OS permissions)

## Security Notes

- Assistant executes system-level actions; run only on trusted machines.
- Use a strong password.
- Voice auth strictness can be tuned (`0` to `100`).

## License

Add your license details here.
