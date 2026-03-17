from __future__ import annotations

import os
import re
import shutil
import subprocess
import time


class LinuxPlatformActions:
    def __init__(self, host):
        self.host = host

    # -----------------------------
    # App/process management
    # -----------------------------

    def close_all_apps(self):
        if shutil.which("wmctrl"):
            output = self.host._run_process(["wmctrl", "-lp"], timeout=20)
            pids = []
            for line in output.splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                pid_text = parts[2].strip()
                if pid_text.isdigit():
                    pid = int(pid_text)
                    if pid > 0 and pid not in pids and pid != os.getpid():
                        pids.append(pid)
            if not pids:
                return "No open application windows were found."
            for pid in pids:
                subprocess.run(["kill", "-15", str(pid)], capture_output=True, text=True, check=False)
            return f"Closed {len(pids)} open application window processes."

        common = ["firefox", "chrome", "chromium", "code", "vlc", "spotify", "brave", "edge", "slack", "discord"]
        closed = []
        for name in common:
            result = subprocess.run(["pkill", "-15", "-x", name], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                closed.append(name)
        if not closed:
            return "No open application windows were found."
        return "Closed applications: " + ", ".join(sorted(set(closed))) + "."

    def list_processes(self):
        output = self.host._run_process(["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"], timeout=20)
        lines = output.splitlines()
        if not lines:
            return "No running processes were found."
        return "\n".join(lines[:221])

    def kill_process_id(self, pid: int):
        self.host._run_process(["kill", "-9", str(pid)], timeout=20)

    def kill_process_name(self, process_name: str):
        if shutil.which("pkill"):
            result = subprocess.run(["pkill", "-9", "-f", process_name], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"No running process matched {process_name}.")
            return
        self.host._run_process(["killall", "-9", process_name], timeout=20)

    # -----------------------------
    # Power actions
    # -----------------------------

    def shutdown_system(self):
        try:
            self.close_all_apps()
        except Exception:
            pass
        time.sleep(0.6)
        if shutil.which("systemctl"):
            os.system("systemctl poweroff")
        else:
            os.system("shutdown -h now")

    def restart_system(self):
        try:
            self.close_all_apps()
        except Exception:
            pass
        time.sleep(0.6)
        if shutil.which("systemctl"):
            os.system("systemctl reboot")
        else:
            os.system("shutdown -r now")

    def sleep_system(self):
        os.system("systemctl suspend")

    # -----------------------------
    # Settings & status
    # -----------------------------

    def set_brightness(self, percent: int):
        if shutil.which("brightnessctl"):
            self.host._run_process(["brightnessctl", "set", f"{percent}%"])
            return
        raise RuntimeError("Brightness control is not available on this system.")

    def set_audio_endpoint(self, *, flow, label, percent, delta, mute_action):
        if shutil.which("pactl"):
            target = "@DEFAULT_SINK@" if flow == 0 else "@DEFAULT_SOURCE@"
            if mute_action == "mute":
                self.host._run_process(["pactl", "set-source-mute" if flow else "set-sink-mute", target, "1"])
                return f"{label} turned off."
            if mute_action == "unmute":
                self.host._run_process(["pactl", "set-source-mute" if flow else "set-sink-mute", target, "0"])
            if percent is not None:
                self.host._run_process(["pactl", "set-source-volume" if flow else "set-sink-volume", target, f"{percent}%"])
                return f"{label} set to {percent} percent."
            if delta is not None:
                amount = f"{abs(int(delta * 100))}%"
                command = [
                    "pactl",
                    "set-source-volume" if flow else "set-sink-volume",
                    target,
                    f"{'+' if delta > 0 else '-'}{amount}",
                ]
                self.host._run_process(command)
                direction_text = "up" if delta > 0 else "down"
                return f"{label} adjusted {direction_text}."
        if shutil.which("amixer"):
            channel = "Capture" if flow else "Master"
            if mute_action == "mute":
                self.host._run_process(["amixer", "set", channel, "mute"], timeout=20)
                return f"{label} turned off."
            if mute_action == "unmute":
                self.host._run_process(["amixer", "set", channel, "unmute"], timeout=20)
            if percent is not None:
                self.host._run_process(["amixer", "set", channel, f"{percent}%"], timeout=20)
                return f"{label} set to {percent} percent."
            if delta is not None:
                sign = "+" if delta > 0 else "-"
                self.host._run_process(["amixer", "set", channel, f"{abs(int(delta * 100))}%{sign}"], timeout=20)
                return f"{label} adjusted {'up' if delta > 0 else 'down'}."
        raise RuntimeError(f"{label} control is not available on this system.")

    def open_setting_panel(self, setting: str):
        if shutil.which("gnome-control-center"):
            panel_map = {
                "wifi": "wifi",
                "bluetooth": "bluetooth",
                "airplane mode": "network",
                "brightness": "display",
                "volume": "sound",
                "microphone": "sound",
                "energy saver": "power",
                "night light": "display",
                "vpn": "network",
                "display": "display",
                "screen saver": "privacy",
            }
            panel = panel_map.get(setting) or "privacy"
            subprocess.Popen(["gnome-control-center", panel])
            return f"Opened {setting or panel} settings."
        raise RuntimeError("Settings panel integration is not available on this Linux desktop.")

    def get_setting_status(self, setting: str):
        if setting == "wifi":
            if shutil.which("nmcli"):
                output = self.host._run_process(["nmcli", "radio", "wifi"], timeout=20).strip().lower()
                return f"Wi-Fi status: {'enabled' if output == 'enabled' else 'disabled'}."
            return "Wi-Fi status is unavailable."
        if setting == "bluetooth":
            if shutil.which("nmcli"):
                output = self.host._run_process(["nmcli", "radio", "bluetooth"], timeout=20).strip().lower()
                return f"Bluetooth status: {'enabled' if output == 'enabled' else 'disabled'}."
            if shutil.which("rfkill"):
                output = self.host._run_process(["rfkill", "list", "bluetooth"], timeout=20).lower()
                blocked = "soft blocked: yes" in output or "hard blocked: yes" in output
                return f"Bluetooth status: {'disabled' if blocked else 'enabled'}."
            return "Bluetooth status is unavailable."
        if setting == "airplane mode":
            if shutil.which("nmcli"):
                output = self.host._run_process(["nmcli", "radio", "all"], timeout=20).lower()
                enabled = sum(1 for line in output.splitlines() if "enabled" in line)
                return "Airplane mode appears to be off." if enabled > 0 else "Airplane mode appears to be on."
            return "Airplane mode status is unavailable."
        if setting == "brightness":
            if shutil.which("brightnessctl"):
                try:
                    value = int(float(self.host._run_process(["brightnessctl", "g"], timeout=20).strip()))
                    maximum = int(float(self.host._run_process(["brightnessctl", "m"], timeout=20).strip()))
                    percent = int(round((value / max(1, maximum)) * 100))
                    return f"Brightness is at {percent} percent."
                except Exception:
                    pass
            if shutil.which("xbacklight"):
                value = self.host._run_process(["xbacklight", "-get"], timeout=20).strip()
                return f"Brightness is at {int(float(value))} percent."
            return "Brightness status is unavailable."
        if setting == "volume":
            if shutil.which("pactl"):
                mute_state = self.host._run_process(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=20).lower()
                if "yes" in mute_state:
                    return "System sound is off."
                vol = self.host._run_process(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=20)
                match = re.search(r"(\\d+)%", vol)
                return f"System sound is at {match.group(1)} percent." if match else "System sound status is unavailable."
            if shutil.which("amixer"):
                output = self.host._run_process(["amixer", "get", "Master"], timeout=20)
                match = re.search(r"\\[(\\d{1,3})%\\]", output)
                muted = "[off]" in output.lower()
                if muted:
                    return "System sound is off."
                return f"System sound is at {match.group(1)} percent." if match else "System sound status is unavailable."
            return "System sound status is unavailable."
        if setting == "microphone":
            if shutil.which("pactl"):
                mute_state = self.host._run_process(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"], timeout=20).lower()
                if "yes" in mute_state:
                    return "Microphone is off."
                vol = self.host._run_process(["pactl", "get-source-volume", "@DEFAULT_SOURCE@"], timeout=20)
                match = re.search(r"(\\d+)%", vol)
                return f"Microphone is at {match.group(1)} percent." if match else "Microphone status is unavailable."
            if shutil.which("amixer"):
                output = self.host._run_process(["amixer", "get", "Capture"], timeout=20)
                match = re.search(r"\\[(\\d{1,3})%\\]", output)
                muted = "[off]" in output.lower()
                if muted:
                    return "Microphone is off."
                return f"Microphone is at {match.group(1)} percent." if match else "Microphone status is unavailable."
            return "Microphone status is unavailable."
        if setting == "energy saver":
            if shutil.which("powerprofilesctl"):
                profile = self.host._run_process(["powerprofilesctl", "get"], timeout=20).strip().lower()
                return "Energy saver is on." if profile == "power-saver" else "Energy saver is off."
            return "Energy saver status is unavailable."
        if setting == "night light":
            if shutil.which("gsettings"):
                value = self.host._run_process(
                    ["gsettings", "get", "org.gnome.settings-daemon.plugins.color", "night-light-enabled"],
                    timeout=20,
                ).strip().lower()
                return "Night light is on." if value == "true" else "Night light is off."
            return "Night light status is unavailable."
        if setting == "vpn":
            if shutil.which("nmcli"):
                output = self.host._run_process(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"], timeout=20)
                vpn_lines = [line.split(":", 1)[0] for line in output.splitlines() if line.endswith(":vpn")]
                if vpn_lines:
                    return f"VPN is connected: {vpn_lines[0]}."
                return "VPN is not connected."
            return "VPN status is unavailable."
        if setting == "display":
            if shutil.which("xrandr"):
                output = self.host._run_process(["xrandr"], timeout=20)
                current = next((line for line in output.splitlines() if "*" in line), "")
                if current:
                    resolution = current.strip().split()[0]
                    return f"Display resolution is {resolution}."
            return "Display status is unavailable."
        if setting == "screen saver":
            if shutil.which("gsettings"):
                value = self.host._run_process(["gsettings", "get", "org.gnome.desktop.screensaver", "lock-enabled"], timeout=20).strip().lower()
                return "Screen saver is on." if value == "true" else "Screen saver is off."
            return "Screen saver status is unavailable."
        raise RuntimeError(f"Status is not available for {setting or 'that setting'}.")

    def set_wifi(self, turn_on: bool):
        if shutil.which("nmcli"):
            self.host._run_process(["nmcli", "radio", "wifi", "on" if turn_on else "off"])
            return
        raise RuntimeError("Wi-Fi control is not available on this system.")

    def set_bluetooth(self, turn_on: bool):
        if shutil.which("nmcli"):
            self.host._run_process(["nmcli", "radio", "bluetooth", "on" if turn_on else "off"], timeout=20)
            return
        if shutil.which("rfkill"):
            self.host._run_process(["rfkill", "unblock" if turn_on else "block", "bluetooth"])
            return
        raise RuntimeError("Bluetooth control is not available on this system.")

    def set_airplane_mode(self, turn_on: bool):
        if shutil.which("nmcli"):
            self.host._run_process(["nmcli", "radio", "all", "off" if turn_on else "on"])
            return
        raise RuntimeError("Airplane mode control is not available on this system.")

    def set_energy_saver(self, turn_on: bool):
        if shutil.which("powerprofilesctl"):
            self.host._run_process(["powerprofilesctl", "set", "power-saver" if turn_on else "balanced"], timeout=20)
            return
        raise RuntimeError("Energy saver control is not available on this system.")

    def set_night_light(self, turn_on: bool):
        if shutil.which("gsettings"):
            self.host._run_process(
                [
                    "gsettings",
                    "set",
                    "org.gnome.settings-daemon.plugins.color",
                    "night-light-enabled",
                    "true" if turn_on else "false",
                ],
                timeout=20,
            )
            return
        raise RuntimeError("Night light control is not available on this system.")
