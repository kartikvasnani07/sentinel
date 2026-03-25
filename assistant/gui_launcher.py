import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _find_repo_root():
    current = Path(__file__).resolve().parent.parent
    for _ in range(5):
        if (current / "apps" / "windows" / "AssistantDesktop").exists():
            return current
        current = current.parent
    return None


def _start_bridge():
    def _run():
        try:
            from .gui_server import run
            run()
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True, name="assistant-gui-bridge")
    thread.start()
    time.sleep(1.2)


def _launch_windows_gui(repo_root: Path):
    app_dir = repo_root / "apps" / "windows" / "AssistantDesktop"
    if not app_dir.exists():
        return False

    exe_candidates = [
        app_dir / "bin" / "Release" / "net8.0-windows" / "AssistantDesktop.exe",
        app_dir / "bin" / "Debug" / "net8.0-windows" / "AssistantDesktop.exe",
    ]
    for exe in exe_candidates:
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=str(app_dir))
            return True

    try:
        subprocess.Popen(["dotnet", "run"], cwd=str(app_dir))
        return True
    except Exception:
        return False


def main():
    _start_bridge()
    if os.name != "nt":
        return
    root = _find_repo_root()
    if root is None:
        return
    _launch_windows_gui(root)


if __name__ == "__main__":
    main()
