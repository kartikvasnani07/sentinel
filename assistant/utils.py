"""
Utility helpers for the desktop assistant.
"""

import os
import random
import subprocess
import sys
from pathlib import Path


GREETING_MESSAGES = [
    "Good to see you again.",
    "I'm online and ready.",
    "Assistant activated.",
    "Welcome back.",
    "All systems online.",
    "Ready when you are.",
    "At your service.",
    "Online and listening.",
]

WAKE_RESPONSES = [
    "Yes?",
    "I'm listening.",
    "Go ahead.",
    "What can I do for you?",
    "How can I help?",
    "Ready.",
    "Listening.",
    "Proceed.",
]

_LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "default": "en",
}


def _autostart_task_name():
    username = str(os.getenv("USERNAME", "user")).strip().lower()
    safe = "".join(char for char in username if char.isalnum()) or "user"
    return f"AIAssistant_{safe}"


_AUTOSTART_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_RUN_VALUE = "AIAssistant"


def random_greeting():
    return random.choice(GREETING_MESSAGES)


def random_wake_response():
    return random.choice(WAKE_RESPONSES)


def resolve_language_code(text):
    normalized = str(text or "").strip().lower()
    return _LANGUAGE_ALIASES.get(normalized)


def _startup_folder():
    appdata = os.getenv("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _shortcut_path():
    return _startup_folder() / "AIAssistant.lnk"


def _launcher_path():
    appdata = os.getenv("APPDATA", "")
    base = Path(appdata) / "AIAssistant" if appdata else (Path.home() / ".assistant")
    return base / "launch_assistant.cmd"


def _run_command(args, timeout=10):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _task_exists():
    result = _run_command(["schtasks", "/Query", "/TN", _autostart_task_name()], timeout=8)
    return result.returncode == 0


def _create_task(launcher):
    result = _run_command(
        [
            "schtasks",
            "/Create",
            "/TN",
            _autostart_task_name(),
            "/SC",
            "ONLOGON",
            "/TR",
            f'"{launcher}"',
            "/F",
            "/IT",
        ],
        timeout=12,
    )
    return result.returncode == 0, (result.stderr or result.stdout or "").strip()


def _delete_task():
    result = _run_command(["schtasks", "/Delete", "/TN", _autostart_task_name(), "/F"], timeout=8)
    return result.returncode == 0


def _is_registry_autostart_enabled():
    result = _run_command(
        [
            "reg",
            "query",
            _AUTOSTART_RUN_KEY,
            "/v",
            _AUTOSTART_RUN_VALUE,
        ],
        timeout=8,
    )
    return result.returncode == 0


def _set_registry_autostart(command):
    result = _run_command(
        [
            "reg",
            "add",
            _AUTOSTART_RUN_KEY,
            "/v",
            _AUTOSTART_RUN_VALUE,
            "/t",
            "REG_SZ",
            "/d",
            command,
            "/f",
        ],
        timeout=10,
    )
    return result.returncode == 0, (result.stderr or result.stdout or "").strip()


def _delete_registry_autostart():
    result = _run_command(
        [
            "reg",
            "delete",
            _AUTOSTART_RUN_KEY,
            "/v",
            _AUTOSTART_RUN_VALUE,
            "/f",
        ],
        timeout=8,
    )
    return result.returncode == 0


def _write_launcher_script():
    launcher = _launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    assistant_root = Path(__file__).resolve().parent.parent
    venv_python = assistant_root / "venv" / "Scripts" / "python.exe"
    configured_python = str(os.getenv("ASSISTANT_PYTHON_EXE", "")).strip()
    if configured_python:
        python_exe = configured_python
    elif venv_python.exists():
        python_exe = str(venv_python)
    else:
        python_exe = str(Path(sys.executable))
    assistant_dir = str(assistant_root)
    script = (
        "@echo off\n"
        f'cd /d "{assistant_dir}"\n'
        f'"{python_exe}" -m assistant.main\n'
    )
    launcher.write_text(script, encoding="utf-8")
    return launcher


def _ensure_startup_shortcut(target_path, working_directory):
    startup = _startup_folder()
    if not startup.exists():
        return False, "Startup folder not found."

    shortcut = _shortcut_path()
    try:
        if shortcut.exists():
            shortcut.unlink()
    except Exception:
        pass

    script = (
        f'$shell = New-Object -ComObject WScript.Shell; '
        f'$shortcut = $shell.CreateShortcut("{shortcut}"); '
        f'$shortcut.TargetPath = "{target_path}"; '
        f'$shortcut.WorkingDirectory = "{working_directory}"; '
        f'$shortcut.Description = "AI Assistant Auto-Start"; '
        f'$shortcut.Save()'
    )
    result = _run_command(["powershell", "-NoProfile", "-Command", script], timeout=12)
    if result.returncode == 0 and shortcut.exists():
        return True, ""
    error = (result.stderr or result.stdout or "").strip() or "Unknown shortcut creation error."
    return False, error


def is_autostart_enabled():
    return _is_registry_autostart_enabled() or _task_exists() or _shortcut_path().exists()


def enable_autostart():
    try:
        launcher = _write_launcher_script()
    except Exception as exc:
        return f"Could not enable auto-start: {exc}"

    registry_enabled, registry_error = _set_registry_autostart(f'"{launcher}"')

    shortcut_enabled, shortcut_error = _ensure_startup_shortcut(
        target_path=str(launcher),
        working_directory=str(launcher.parent),
    )

    if registry_enabled and shortcut_enabled:
        return "Auto-start enabled for immediate launch on login (registry + startup fallback)."
    if registry_enabled:
        return "Auto-start enabled for immediate launch on login."
    if shortcut_enabled:
        detail = f" Registry hook unavailable: {registry_error}" if registry_error else ""
        return f"Auto-start enabled with startup folder shortcut.{detail}"

    details = " ".join(piece for piece in [registry_error, shortcut_error] if piece).strip()
    if not details:
        details = "Auto-start setup failed."
    return f"Could not enable auto-start: {details}"


def disable_autostart():
    registry_removed = _delete_registry_autostart()
    task_removed = _delete_task()
    shortcut_removed = False
    launcher_removed = False

    shortcut = _shortcut_path()
    launcher = _launcher_path()

    try:
        if shortcut.exists():
            shortcut.unlink()
            shortcut_removed = True
    except Exception:
        pass

    try:
        if launcher.exists():
            launcher.unlink()
            launcher_removed = True
    except Exception:
        pass

    if registry_removed or task_removed or shortcut_removed or launcher_removed:
        return "Auto-start disabled."
    return "Auto-start was not enabled."


def draw_file_tree(root_path, max_depth=None, max_items=None):
    root = Path(root_path).resolve()
    if not root.exists():
        return f"Path not found: {root}"

    cwd = Path.cwd().resolve()
    root_marker = " -> current" if root == cwd else ""
    lines = [f"{root}{root_marker}"]
    if root != cwd:
        lines.append(f"-> current: {cwd}")
    seen = [0]

    def _walk(directory, prefix, depth):
        if max_depth is not None and depth >= max_depth:
            return
        if max_items is not None and seen[0] >= max_items:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}[permission denied]")
            return
        except OSError:
            lines.append(f"{prefix}[unavailable]")
            return

        for index, entry in enumerate(entries):
            seen[0] += 1
            if max_items is not None and seen[0] > max_items:
                lines.append(f"{prefix}... (truncated)")
                return

            is_last = index == len(entries) - 1
            connector = "\\-- " if is_last else "|-- "
            marker = " -> current" if entry.resolve() == cwd else ""

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/{marker}")
                extension = "    " if is_last else "|   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}{marker}")

    _walk(root, "", 0)
    return "\n".join(lines)
