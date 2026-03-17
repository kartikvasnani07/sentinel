"""
One-shot installer for the desktop assistant.

Usage:
    python -m assistant.installer
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen


VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"


def _print(message: str) -> None:
    print(f"[installer] {message}")


def _run(command: list[str], *, check: bool = True, shell: bool = False) -> int:
    rendered = " ".join(command) if isinstance(command, list) else str(command)
    _print(f"Running: {rendered}")
    result = subprocess.run(command, check=False, shell=shell)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {rendered}")
    return result.returncode


def _ensure_python_dependencies(project_root: Path) -> None:
    requirements = project_root / "requirements.txt"
    if not requirements.exists():
        raise RuntimeError(f"requirements.txt not found at {requirements}")
    _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    _run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])


def _ensure_console_scripts(project_root: Path) -> None:
    setup_file = project_root / "setup.py"
    if not setup_file.exists():
        return
    _run([sys.executable, "-m", "pip", "install", "-e", str(project_root)])


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=60) as response:  # nosec - trusted URL for installer asset
        data = response.read()
    destination.write_bytes(data)


def _ensure_vosk_model(project_root: Path) -> None:
    models_dir = project_root / "models" / "vosk"
    model_dir = models_dir / VOSK_MODEL_NAME
    if model_dir.exists():
        _print(f"Vosk model already present: {model_dir}")
        return

    zip_path = models_dir / f"{VOSK_MODEL_NAME}.zip"
    _print("Downloading Vosk wake-word model...")
    _download_file(VOSK_MODEL_URL, zip_path)
    _print("Extracting Vosk model...")
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(models_dir)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    _print(f"Vosk model ready: {model_dir}")


def _ensure_whisper_local_model(model_name: str = "small") -> None:
    preload_script = (
        "from faster_whisper import WhisperModel; "
        f"WhisperModel('{model_name}', device='cpu', compute_type='int8', local_files_only=False); "
        "print('Whisper model ready')"
    )
    _print(f"Preloading local Whisper model: {model_name}")
    _run([sys.executable, "-c", preload_script], check=False)


def _install_ollama_if_missing() -> bool:
    if shutil.which("ollama"):
        return True

    system_name = platform.system().lower()
    _print("Ollama not found. Attempting automatic installation...")
    try:
        if system_name.startswith("win"):
            if shutil.which("winget"):
                _run(["winget", "install", "-e", "--id", "Ollama.Ollama"], check=False)
            else:
                _print("winget not available. Please install Ollama manually from https://ollama.com/download")
        elif system_name.startswith("linux"):
            if shutil.which("curl"):
                _run(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"], check=False, shell=False)
            else:
                _print("curl not available. Please install Ollama manually from https://ollama.com/download")
        else:
            _print("Automatic Ollama install is not configured for this OS.")
    except Exception as exc:
        _print(f"Ollama auto-install attempt failed: {exc}")

    return bool(shutil.which("ollama"))


def _ensure_ollama_models(chat_model: str, code_model: str) -> None:
    if not _install_ollama_if_missing():
        _print("Skipping Ollama model pull because Ollama is unavailable.")
        return

    _print(f"Pulling Ollama chat model: {chat_model}")
    _run(["ollama", "pull", chat_model], check=False)
    if code_model and code_model != chat_model:
        _print(f"Pulling Ollama coding model: {code_model}")
        _run(["ollama", "pull", code_model], check=False)


def _platform_summary() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install assistant dependencies and local models.")
    parser.add_argument("--skip-ollama", action="store_true", help="Skip Ollama installation/model pulls.")
    parser.add_argument("--skip-whisper", action="store_true", help="Skip local Whisper model preload.")
    parser.add_argument("--skip-vosk", action="store_true", help="Skip Vosk model download.")
    parser.add_argument("--chat-model", default=os.getenv("OLLAMA_MODEL", "llama3.1:latest"))
    parser.add_argument("--code-model", default=os.getenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b"))
    parser.add_argument("--whisper-model", default=os.getenv("WHISPER_MODEL_SIZE", "small"))
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    _print(f"Detected platform: {_platform_summary()}")
    _print(f"Project root: {project_root}")

    try:
        _ensure_python_dependencies(project_root)
        _ensure_console_scripts(project_root)
        if not args.skip_vosk:
            _ensure_vosk_model(project_root)
        if not args.skip_whisper:
            _ensure_whisper_local_model(args.whisper_model)
        if not args.skip_ollama:
            _ensure_ollama_models(args.chat_model, args.code_model)
        _print("Installation completed.")
        _print("You can now run: python -m assistant.main")
        return 0
    except Exception as exc:
        _print(f"Installation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
