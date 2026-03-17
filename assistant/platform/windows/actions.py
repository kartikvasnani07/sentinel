from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from ctypes import POINTER, cast

try:
    import ctypes
except Exception:  # pragma: no cover - optional
    ctypes = None

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except Exception:  # pragma: no cover - optional dependency
    CLSCTX_ALL = None
    AudioUtilities = None
    IAudioEndpointVolume = None


class WindowsPlatformActions:
    def __init__(self, host):
        self.host = host

    # -----------------------------
    # App/process management
    # -----------------------------

    def close_all_apps(self):
        script = r"""
$excluded = @('python', 'python3', 'powershell', 'pwsh', 'cmd', 'conhost')
$closed = @()
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowHandle -ne 0 -and $_.ProcessName -and ($excluded -notcontains $_.ProcessName.ToLower())
} | ForEach-Object {
    try {
        Stop-Process -Id $_.Id -Force -ErrorAction Stop
        $closed += $_.ProcessName
    } catch {}
}
$unique = $closed | Sort-Object -Unique
if ($unique.Count -eq 0) {
    'No open application windows were found.'
} else {
    'Closed applications: ' + ($unique -join ', ')
}
""".strip()
        return self.host._powershell(script, timeout=30)

    def list_processes(self):
        script = r"""
$rows = Get-Process -ErrorAction SilentlyContinue |
    Sort-Object -Property CPU -Descending |
    Select-Object -First 220 -Property Id, ProcessName, CPU, WorkingSet64
$lines = @('PID`tProcess`tCPU(s)`tMemory(MB)')
foreach ($row in $rows) {
    $cpu = if ($row.CPU -eq $null) { 0 } else { [Math]::Round([double]$row.CPU, 2) }
    $mem = [Math]::Round(([double]$row.WorkingSet64 / 1MB), 1)
    $lines += ("{0}`t{1}`t{2}`t{3}" -f $row.Id, $row.ProcessName, $cpu, $mem)
}
$lines -join [Environment]::NewLine
""".strip()
        return self.host._powershell(script, timeout=25)

    def kill_process_id(self, pid: int):
        self.host._run_process(["taskkill", "/PID", str(pid), "/F"], timeout=20)

    def kill_process_name(self, process_name: str, candidates: list[str]):
        tried = []
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            image_name = candidate if candidate.lower().endswith(".exe") else f"{candidate}.exe"
            if image_name.lower() in tried:
                continue
            tried.append(image_name.lower())
            result = subprocess.run(["taskkill", "/IM", image_name, "/F"], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return
        raise RuntimeError(f"No running process matched {process_name}.")

    # -----------------------------
    # Power actions
    # -----------------------------

    def shutdown_system(self):
        try:
            self.close_all_apps()
        except Exception:
            pass
        time.sleep(0.6)
        os.system("shutdown /s /t 0")

    def restart_system(self):
        try:
            self.close_all_apps()
        except Exception:
            pass
        time.sleep(0.6)
        os.system("shutdown /r /t 0")

    def sleep_system(self):
        if ctypes is not None:
            try:
                result = ctypes.windll.powrprof.SetSuspendState(False, True, False)
                if result != 0:
                    return
            except Exception:
                pass
        self.host._run_process(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=20)

    # -----------------------------
    # Settings & status
    # -----------------------------

    def set_brightness(self, percent: int):
        script = (
            "$monitors = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods; "
            f"foreach ($m in $monitors) {{ $m.WmiSetBrightness(1,{percent}) | Out-Null }}"
        )
        self.host._powershell(script)

    def set_audio_endpoint(self, *, flow, label, percent, delta, mute_action):
        state = self._python_audio_state(flow, percent=percent, delta=delta, mute_action=mute_action)
        if state is None:
            payload = self.host._powershell(
                self._windows_audio_endpoint_script(flow, percent=percent, delta=delta, mute_action=mute_action),
                timeout=25,
            )
            state = json.loads(payload) if payload else {}
            if not isinstance(state, dict):
                state = {}
        final_level = int(state.get("level") or state.get("Level") or 0)
        is_muted = bool(state.get("muted") if "muted" in state else state.get("Muted", False))
        if is_muted or final_level == 0:
            return f"{label} turned off."
        return f"{label} set to {final_level} percent."

    def open_setting_panel(self, setting: str):
        uri_map = {
            "wifi": "ms-settings:network-wifi",
            "bluetooth": "ms-settings:bluetooth",
            "airplane mode": "ms-settings:network-airplanemode",
            "brightness": "ms-settings:display",
            "volume": "ms-settings:sound",
            "microphone": "ms-settings:privacy-microphone",
            "energy saver": "ms-settings:powersleep",
            "night light": "ms-settings:nightlight",
            "vpn": "ms-settings:network-vpn",
            "display": "ms-settings:display",
        }
        if setting == "screen saver":
            subprocess.Popen(["control.exe", "desk.cpl,,@screensaver"])
            return "Opened Screen Saver settings."
        uri = uri_map.get(setting)
        if not uri:
            raise RuntimeError(f"Settings panel not mapped for {setting or 'that request'}.")
        os.startfile(uri)
        return f"Opened {setting} settings."

    def get_setting_status(self, setting: str):
        if setting == "wifi":
            output = self.host._run_process(["netsh", "interface", "show", "interface"], timeout=20)
            for line in output.splitlines():
                if "Wi-Fi" in line or "Wireless" in line:
                    return f"Wi-Fi status: {'enabled' if 'Enabled' in line else 'disabled'}."
            return "Wi-Fi status is unavailable."
        if setting == "bluetooth":
            adapters = self._windows_bluetooth_adapters()
            if not adapters:
                return "Bluetooth status is unavailable."
            active = next((item for item in adapters if str(item.get("Status") or "").strip().lower() == "ok"), None)
            if active:
                return f"Bluetooth is on ({active.get('FriendlyName')})."
            name = str(adapters[0].get("FriendlyName") or "adapter").strip()
            return f"Bluetooth is off ({name})."
        if setting == "airplane mode":
            output = self.host._run_process(["netsh", "interface", "show", "interface"], timeout=20)
            enabled_count = sum(
                1
                for line in output.splitlines()
                if "Enabled" in line and ("Wi-Fi" in line or "Bluetooth" in line or "Wireless" in line)
            )
            return "Airplane mode appears to be on." if enabled_count == 0 else "Airplane mode appears to be off."
        if setting == "brightness":
            script = "(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness | Select-Object -First 1 -ExpandProperty CurrentBrightness)"
            value = self.host._powershell(script)
            return f"Brightness is at {value.strip()} percent."
        if setting == "volume":
            return self._windows_audio_status(0, "System sound")
        if setting == "microphone":
            return self._windows_audio_status(1, "Microphone")
        if setting == "energy saver":
            output = self.host._run_process(["powercfg", "/getactivescheme"], timeout=20)
            return "Energy saver is on." if self.host.POWER_SAVER_GUID.lower() in output.lower() else "Energy saver is off."
        if setting == "night light":
            return "Night Light status is not directly available in this build."
        if setting == "vpn":
            script = (
                "$vpn = Get-VpnConnection -ErrorAction SilentlyContinue | Where-Object { $_.ConnectionStatus -eq 'Connected' } | "
                "Select-Object -First 1 -ExpandProperty Name; "
                "if ($vpn) { \"VPN is connected: $vpn.\" } else { 'VPN is not connected.' }"
            )
            return self.host._powershell(script)
        if setting == "display":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "\"Display resolution is $($bounds.Width) by $($bounds.Height).\""
            )
            return self.host._powershell(script)
        if setting == "screen saver":
            script = "(Get-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name ScreenSaveActive).ScreenSaveActive"
            value = self.host._powershell(script).strip()
            return "Screen saver is on." if value == "1" else "Screen saver is off."
        raise RuntimeError(f"Status is not available for {setting or 'that setting'}.")

    def set_wifi(self, turn_on: bool):
        errors = []
        for name in self._wireless_interface_names():
            try:
                self.host._run_process(
                    ["netsh", "interface", "set", "interface", name, f"admin={'enabled' if turn_on else 'disabled'}"],
                    timeout=20,
                )
                return
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError(errors[0] if errors else "No Wi-Fi interface was found.")

    def set_bluetooth(self, turn_on: bool):
        adapters = self._windows_bluetooth_adapters()
        if not adapters:
            raise RuntimeError("Bluetooth adapter not found.")
        errors = []
        for adapter in adapters:
            command = "Enable-PnpDevice" if turn_on else "Disable-PnpDevice"
            script = f"{command} -InstanceId {self.host._ps_quote(adapter['InstanceId'])} -Confirm:$false -ErrorAction Stop | Out-Null"
            try:
                self.host._powershell(script, timeout=25)
            except Exception as exc:
                errors.append(str(exc).splitlines()[0].strip())
        if errors:
            os.startfile("ms-settings:bluetooth")
            joined = "; ".join(errors)
            raise RuntimeError(
                f"Bluetooth {'enable' if turn_on else 'disable'} requires device permission or administrator access. "
                f"Opened Bluetooth settings. Details: {joined}"
            )

    def set_airplane_mode(self, turn_on: bool):
        updates = []
        errors = []
        try:
            self.set_wifi(not turn_on)
            updates.append("Wi-Fi updated.")
        except Exception as exc:
            errors.append(str(exc).splitlines()[0].strip())
        try:
            self.set_bluetooth(not turn_on)
            updates.append("Bluetooth updated.")
        except Exception as exc:
            errors.append(str(exc).splitlines()[0].strip())
        if errors and not updates:
            raise RuntimeError("; ".join(errors))
        if errors:
            return f"Airplane mode changed with partial success. {' '.join(updates)} {'; '.join(errors)}"
        return f"Airplane mode turned {'on' if turn_on else 'off'}."

    def set_energy_saver(self, turn_on: bool):
        self.host._run_process(
            ["powercfg", "/setactive", self.host.POWER_SAVER_GUID if turn_on else self.host.BALANCED_POWER_GUID],
            timeout=20,
        )

    def set_night_light(self, turn_on: bool):
        os.startfile("ms-settings:nightlight")

    # -----------------------------
    # Windows-only helpers
    # -----------------------------

    def _wireless_interface_names(self):
        output = self.host._run_process(["netsh", "interface", "show", "interface"], timeout=20)
        names = []
        for line in output.splitlines():
            if "Dedicated" not in line and "Wireless" not in line and "Wi-Fi" not in line:
                continue
            parts = [segment for segment in re.split(r"\s{2,}", line.strip()) if segment]
            if parts:
                names.append(parts[-1])
        if not names:
            names = ["Wi-Fi", "WiFi", "Wireless Network Connection"]
        return names

    def _windows_bluetooth_adapters(self):
        script = r"""
$devices = @(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object {
    $_.InstanceId -match '^(USB|PCI|ACPI)\\' -and
    $_.FriendlyName -and
    $_.FriendlyName -notmatch 'Enumerator|RFCOMM|Transport|Service|Protocol TDI|Device \(RFCOMM'
} | Select-Object Status, FriendlyName, InstanceId)
$devices | ConvertTo-Json -Depth 4 -Compress
""".strip()
        payload = self.host._powershell(script, timeout=20)
        if not payload:
            return []
        data = json.loads(payload)
        if isinstance(data, dict):
            data = [data]
        return [item for item in data if isinstance(item, dict) and item.get("InstanceId")]

    def _windows_audio_endpoint_script(self, flow, *, percent=None, delta=None, mute_action=""):
        target_level = "" if percent is None else max(0, min(100, int(percent))) / 100.0
        delta_level = 0.0 if delta is None else float(delta)
        return f"""
$flow = {int(flow)}
$targetLevel = {'$null' if percent is None else target_level}
$delta = {delta_level}
$muteAction = {self.host._ps_quote(mute_action)}
if (-not ([System.Management.Automation.PSTypeName]'AudioUtil.AudioManager').Type) {{
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace AudioUtil {{
    public enum EDataFlow {{ eRender, eCapture, eAll }}
    public enum ERole {{ eConsole, eMultimedia, eCommunications }}
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {{
        int NotImpl1();
        [PreserveSig] int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice ppDevice);
    }}
    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {{
        [PreserveSig] int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    }}
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume {{
        int RegisterControlChangeNotify(IntPtr pNotify);
        int UnregisterControlChangeNotify(IntPtr pNotify);
        int GetChannelCount(out uint pnChannelCount);
        int SetMasterVolumeLevel(float fLevelDB, Guid pguidEventContext);
        int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
        int GetMasterVolumeLevel(out float pfLevelDB);
        int GetMasterVolumeLevelScalar(out float pfLevel);
        int SetChannelVolumeLevel(uint nChannel, float fLevelDB, Guid pguidEventContext);
        int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, Guid pguidEventContext);
        int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
        int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
        int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
        int GetMute(out bool pbMute);
        int GetVolumeStepInfo(out uint pnStep, out uint pnStepCount);
        int VolumeStepUp(Guid pguidEventContext);
        int VolumeStepDown(Guid pguidEventContext);
        int QueryHardwareSupport(out uint pdwHardwareSupportMask);
        int GetVolumeRange(out float pflVolumeMindB, out float pflVolumeMaxdB, out float pflVolumeIncrementdB);
    }}
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorComObject {{ }}
    public static class AudioManager {{
        public static IAudioEndpointVolume GetEndpoint(int flow) {{
            IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
            IMMDevice device;
            Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint((EDataFlow)flow, ERole.eMultimedia, out device));
            Guid iid = typeof(IAudioEndpointVolume).GUID;
            object endpoint;
            Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out endpoint));
            return (IAudioEndpointVolume)endpoint;
        }}
        public static float GetLevel(int flow) {{
            float level;
            Marshal.ThrowExceptionForHR(GetEndpoint(flow).GetMasterVolumeLevelScalar(out level));
            return level;
        }}
        public static bool GetMute(int flow) {{
            bool muted;
            Marshal.ThrowExceptionForHR(GetEndpoint(flow).GetMute(out muted));
            return muted;
        }}
        public static void SetLevel(int flow, float level) {{
            Marshal.ThrowExceptionForHR(GetEndpoint(flow).SetMasterVolumeLevelScalar(Math.Max(0, Math.Min(1, level)), Guid.Empty));
        }}
        public static void SetMute(int flow, bool muted) {{
            Marshal.ThrowExceptionForHR(GetEndpoint(flow).SetMute(muted, Guid.Empty));
        }}
    }}
}}
"@ -Language CSharp
}}
$level = [AudioUtil.AudioManager]::GetLevel($flow)
if ($muteAction -eq 'mute') {{
    [AudioUtil.AudioManager]::SetMute($flow, $true)
}}
elseif ($muteAction -eq 'unmute') {{
    [AudioUtil.AudioManager]::SetMute($flow, $false)
}}
if ($targetLevel -ne $null) {{
    [AudioUtil.AudioManager]::SetLevel($flow, [float]$targetLevel)
    if ($targetLevel -gt 0) {{
        [AudioUtil.AudioManager]::SetMute($flow, $false)
    }}
}}
elseif ($delta -ne 0) {{
    $newLevel = [Math]::Max(0, [Math]::Min(1, $level + $delta))
    [AudioUtil.AudioManager]::SetLevel($flow, [float]$newLevel)
    if ($newLevel -gt 0) {{
        [AudioUtil.AudioManager]::SetMute($flow, $false)
    }}
}}
$finalLevel = [Math]::Round([AudioUtil.AudioManager]::GetLevel($flow) * 100)
$finalMuted = [AudioUtil.AudioManager]::GetMute($flow)
@{{ level = $finalLevel; muted = $finalMuted }} | ConvertTo-Json -Compress
""".strip()

    def _python_audio_endpoint(self, flow):
        if AudioUtilities is None or IAudioEndpointVolume is None or CLSCTX_ALL is None:
            return None
        if flow == 0:
            device = AudioUtilities.GetSpeakers()
            endpoint = getattr(device, "EndpointVolume", None)
            if endpoint is not None:
                return endpoint
        else:
            if not hasattr(AudioUtilities, "GetMicrophone"):
                return None
            device = AudioUtilities.GetMicrophone()
            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        return None

    def _python_audio_state(self, flow, *, percent=None, delta=None, mute_action=""):
        endpoint = self._python_audio_endpoint(flow)
        if endpoint is None:
            return None
        if mute_action == "mute":
            endpoint.SetMute(1, None)
        elif mute_action == "unmute":
            endpoint.SetMute(0, None)
        if percent is not None:
            scalar = max(0.0, min(1.0, float(percent) / 100.0))
            endpoint.SetMasterVolumeLevelScalar(scalar, None)
            if scalar > 0:
                endpoint.SetMute(0, None)
        elif delta is not None and delta != 0:
            current = float(endpoint.GetMasterVolumeLevelScalar())
            scalar = max(0.0, min(1.0, current + float(delta)))
            endpoint.SetMasterVolumeLevelScalar(scalar, None)
            if scalar > 0:
                endpoint.SetMute(0, None)
        final_level = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
        final_muted = bool(endpoint.GetMute())
        return {"level": final_level, "muted": final_muted}

    def _windows_audio_status(self, flow, label):
        state = self._python_audio_state(flow, percent=None, delta=None, mute_action="")
        if state is None:
            script = self._windows_audio_endpoint_script(flow, percent=None, delta=None, mute_action="")
            payload = self.host._powershell(script, timeout=20)
            state = json.loads(payload) if payload else {}
            if not isinstance(state, dict):
                state = {}
        level = int(state.get("level") or state.get("Level") or 0)
        muted = bool(state.get("muted") if "muted" in state else state.get("Muted", False))
        if muted or level == 0:
            return f"{label} is off."
        return f"{label} is at {level} percent."
