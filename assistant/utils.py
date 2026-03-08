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


def is_autostart_enabled():
    return _shortcut_path().exists()


def enable_autostart():
    startup = _startup_folder()
    if not startup.exists():
        return "Startup folder not found. Auto-start is not available."

    if _shortcut_path().exists():
        return "Auto-start is already enabled."

    shortcut = _shortcut_path()
    python_exe = sys.executable
    assistant_dir = str(Path(__file__).resolve().parent.parent)

    script = (
        f'$shell = New-Object -ComObject WScript.Shell; '
        f'$shortcut = $shell.CreateShortcut("{shortcut}"); '
        f'$shortcut.TargetPath = "{python_exe}"; '
        f'$shortcut.Arguments = "-m assistant.main"; '
        f'$shortcut.WorkingDirectory = "{assistant_dir}"; '
        f'$shortcut.Description = "AI Assistant Auto-Start"; '
        f'$shortcut.Save()'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "Auto-start enabled. The assistant will launch on login."
    except Exception as exc:
        return f"Could not enable auto-start: {exc}"


def disable_autostart():
    shortcut = _shortcut_path()
    if not shortcut.exists():
        return "Auto-start was not enabled."

    try:
        shortcut.unlink()
        return "Auto-start disabled."
    except Exception as exc:
        return f"Could not disable auto-start: {exc}"


def draw_file_tree(root_path, max_depth=3, max_items=200):
    root = Path(root_path).resolve()
    if not root.exists():
        return f"Path not found: {root}"

    root_marker = " -> current" if root == Path.cwd().resolve() else ""
    lines = [f"{root}{root_marker}"]
    seen = [0]

    def _walk(directory, prefix, depth):
        if depth >= max_depth or seen[0] >= max_items:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}[permission denied]")
            return

        for index, entry in enumerate(entries):
            seen[0] += 1
            if seen[0] > max_items:
                lines.append(f"{prefix}... (truncated)")
                return

            is_last = index == len(entries) - 1
            connector = "\\-- " if is_last else "|-- "
            marker = " -> current" if entry.resolve() == Path.cwd().resolve() else ""

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/{marker}")
                extension = "    " if is_last else "|   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}{marker}")

    _walk(root, "", 0)
    return "\n".join(lines)
