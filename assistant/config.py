"""
Persistent YAML-backed configuration for the desktop assistant.

Configuration is stored at ``~/.assistant/config.yaml`` and is normalized on
load so runtime code can depend on a consistent shape.
"""

import os
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


_CONFIG_DIR = Path.home() / ".assistant"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"
UI_MODES = {"waves", "bubble"}

VOICE_PRESETS = {
    "jarvis": {
        "description": "JARVIS - Refined British male, calm and authoritative",
        "edge_voice": "en-GB-RyanNeural",
        "edge_rate": "-5%",
        "edge_pitch": "-2Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 170,
        "pyttsx3_voice_keyword": "david",
    },
    "tars": {
        "description": "TARS - Deep, calm American male with dry delivery",
        "edge_voice": "en-US-DavisNeural",
        "edge_rate": "-8%",
        "edge_pitch": "-4Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 160,
        "pyttsx3_voice_keyword": "david",
    },
    "friday": {
        "description": "FRIDAY - Professional female, clear and helpful",
        "edge_voice": "en-IE-EmilyNeural",
        "edge_rate": "+0%",
        "edge_pitch": "+0Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 180,
        "pyttsx3_voice_keyword": "zira",
    },
    "edith": {
        "description": "EDITH - Warm, articulate female assistant",
        "edge_voice": "en-US-AriaNeural",
        "edge_rate": "+0%",
        "edge_pitch": "+1Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 180,
        "pyttsx3_voice_keyword": "zira",
    },
    "nova": {
        "description": "Nova - Futuristic American male, smooth",
        "edge_voice": "en-US-GuyNeural",
        "edge_rate": "-3%",
        "edge_pitch": "-1Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 175,
        "pyttsx3_voice_keyword": "david",
    },
    "aurora": {
        "description": "Aurora - Warm American female, conversational",
        "edge_voice": "en-US-JennyNeural",
        "edge_rate": "+0%",
        "edge_pitch": "+0Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 180,
        "pyttsx3_voice_keyword": "zira",
    },
    "atlas": {
        "description": "Atlas - Authoritative British male, butler-style",
        "edge_voice": "en-GB-ThomasNeural",
        "edge_rate": "-5%",
        "edge_pitch": "-3Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 165,
        "pyttsx3_voice_keyword": "david",
    },
    "bmo": {
        "description": "BMO - Bright, playful, lightweight synthetic voice",
        "edge_voice": "en-US-AnaNeural",
        "edge_rate": "+18%",
        "edge_pitch": "+8Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 205,
        "pyttsx3_voice_keyword": "zira",
    },
    "vision": {
        "description": "Vision - Smooth, precise, composed",
        "edge_voice": "en-GB-SoniaNeural",
        "edge_rate": "-2%",
        "edge_pitch": "-1Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 175,
        "pyttsx3_voice_keyword": "zira",
    },
    "cortana": {
        "description": "Cortana - Clean, confident, digital assistant",
        "edge_voice": "en-US-JennyNeural",
        "edge_rate": "+3%",
        "edge_pitch": "+2Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 185,
        "pyttsx3_voice_keyword": "zira",
    },
    "hal": {
        "description": "HAL - Measured, deliberate, restrained",
        "edge_voice": "en-US-TonyNeural",
        "edge_rate": "-12%",
        "edge_pitch": "-5Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 150,
        "pyttsx3_voice_keyword": "david",
    },
    "eve": {
        "description": "EVE - Soft, airy, futuristic",
        "edge_voice": "en-AU-NatashaNeural",
        "edge_rate": "+8%",
        "edge_pitch": "+5Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 190,
        "pyttsx3_voice_keyword": "zira",
    },
    "oracle": {
        "description": "Oracle - Warm, deliberate, wise",
        "edge_voice": "en-CA-ClaraNeural",
        "edge_rate": "-1%",
        "edge_pitch": "+1Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 176,
        "pyttsx3_voice_keyword": "zira",
    },
    "sentinel": {
        "description": "Sentinel - Dense, tactical, command-room delivery",
        "edge_voice": "en-US-ChristopherNeural",
        "edge_rate": "-6%",
        "edge_pitch": "-4Hz",
        "edge_volume": "+0%",
        "pyttsx3_rate": 162,
        "pyttsx3_voice_keyword": "david",
    },
}

SUPPORTED_LANGUAGES = {"en": "English"}
LANGUAGE_TTS_VOICES = {"en": None}
LANGUAGE_FULL_NAMES = {"en": "English"}

_DEFAULTS = {
    "assistant_name": "friday",
    "wake_variants": [],
    "wake_response_enabled": True,
    "waves_enabled": True,
    "ui_mode": "waves",
    "wake_sensitivity": 65,
    "access_level": "full",
    "model_preference": "auto",
    "password_hash": "",
    "voice_auth_threshold": 0,
    "voice_sample_path": "",
    "humor_level": 50,
    "language": "en",
    "voice_preset": "jarvis",
    "auto_start": False,
    "is_setup_complete": False,
    "openweather_api_key": "",
    "default_location": "",
    "default_create_path": "",
    "open_on_startup": False,
    "clap_launch_enabled": False,
    "startup_commands": [],
}


def _clamp_percentage(value, default):
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


class AssistantConfig:
    """Load, update, normalize, and persist assistant configuration."""

    def __init__(self, path=None):
        self.path = Path(path) if path else _CONFIG_FILE
        self._data = dict(_DEFAULTS)
        self._load()
        self._normalize()

    def is_first_time_setup(self):
        return not bool(self._data.get("is_setup_complete"))

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self._normalize()
        self._save()

    def update(self, mapping):
        self._data.update(mapping or {})
        self._normalize()
        self._save()

    def as_dict(self):
        return dict(self._data)

    def reset_factory_state(self, delete_voice_sample=True):
        voice_sample_path = str(self._data.get("voice_sample_path") or "").strip()
        self._data = dict(_DEFAULTS)
        self._normalize()
        if delete_voice_sample and voice_sample_path:
            try:
                sample_path = Path(voice_sample_path)
                if sample_path.exists():
                    sample_path.unlink()
            except OSError:
                pass
        self._save()

    def _normalize(self):
        self._data.pop("custom_voice_profiles", None)
        self._data.pop("active_custom_voice", None)
        legacy_preset = self._data.get("active_voice_preset")
        if legacy_preset and not self._data.get("voice_preset"):
            self._data["voice_preset"] = legacy_preset

        if not str(self._data.get("openweather_api_key") or "").strip():
            env_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
            if env_key:
                self._data["openweather_api_key"] = env_key
        if not str(self._data.get("default_location") or "").strip():
            env_loc = os.getenv("ASSISTANT_DEFAULT_LOCATION", "").strip()
            if env_loc:
                self._data["default_location"] = env_loc
        if not str(self._data.get("default_create_path") or "").strip():
            env_path = os.getenv("ASSISTANT_DEFAULT_CREATE_PATH", "").strip()
            if env_path:
                self._data["default_create_path"] = env_path
        if not str(self._data.get("default_create_path") or "").strip():
            self._data["default_create_path"] = "desktop"

        assistant_name = str(self._data.get("assistant_name") or "friday").strip().lower()
        assistant_name = " ".join(assistant_name.split()) or "friday"
        self._data["assistant_name"] = assistant_name

        variants = self._data.get("wake_variants")
        if not isinstance(variants, list):
            variants = []
        self._data["wake_variants"] = [str(item).strip().lower() for item in variants if str(item).strip()]
        self._data["wake_response_enabled"] = bool(self._data.get("wake_response_enabled", True))
        self._data["waves_enabled"] = bool(self._data.get("waves_enabled", True))
        ui_mode = str(self._data.get("ui_mode") or "waves").strip().lower()
        if ui_mode not in UI_MODES:
            ui_mode = "waves"
        self._data["ui_mode"] = ui_mode
        self._data["wake_sensitivity"] = _clamp_percentage(
            self._data.get("wake_sensitivity"),
            _DEFAULTS["wake_sensitivity"],
        )

        access_level = str(self._data.get("access_level") or "full").strip().lower()
        if access_level not in {"read", "write", "full"}:
            access_level = "full"
        self._data["access_level"] = access_level

        model_preference = str(self._data.get("model_preference") or "auto").strip().lower()
        if not model_preference:
            model_preference = "auto"
        self._data["model_preference"] = model_preference

        self._data["openweather_api_key"] = str(self._data.get("openweather_api_key") or "").strip()
        self._data["default_location"] = str(self._data.get("default_location") or "").strip()
        self._data["default_create_path"] = str(self._data.get("default_create_path") or "").strip()
        self._data["open_on_startup"] = bool(self._data.get("open_on_startup"))
        startup_commands = self._data.get("startup_commands")
        if not isinstance(startup_commands, list):
            startup_commands = []
        cleaned_commands = []
        for item in startup_commands:
            value = str(item or "").strip()
            if value:
                cleaned_commands.append(value)
        self._data["startup_commands"] = cleaned_commands

        self._data["voice_auth_threshold"] = _clamp_percentage(
            self._data.get("voice_auth_threshold"),
            _DEFAULTS["voice_auth_threshold"],
        )
        self._data["humor_level"] = _clamp_percentage(
            self._data.get("humor_level"),
            _DEFAULTS["humor_level"],
        )

        self._data["language"] = "en"

        preset = str(self._data.get("voice_preset") or "jarvis").strip().lower()
        if preset not in VOICE_PRESETS:
            preset = "jarvis"
        self._data["voice_preset"] = preset

        self._data["password_hash"] = str(self._data.get("password_hash") or "").strip()
        self._data["voice_sample_path"] = str(self._data.get("voice_sample_path") or "").strip()
        self._data["auto_start"] = bool(self._data.get("auto_start"))
        self._data["is_setup_complete"] = bool(self._data.get("is_setup_complete"))

    def _load(self):
        self._load_from_path(self.path)
        local_path = (Path.cwd() / "config.yaml").resolve()
        if local_path != self.path:
            self._load_from_path(local_path)

    def _load_from_path(self, path):
        if not path.exists():
            return
        try:
            if yaml is not None:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle)
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            else:
                self._load_fallback_path(path)
        except Exception as exc:
            print(f"[config] Failed to load {path}: {exc}")

    def _load_fallback(self):
        self._load_fallback_path(self.path)

    def _load_fallback_path(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue

                key, _, raw_value = line.partition(":")
                key = key.strip()
                value = raw_value.strip().strip("'\"")
                if key not in _DEFAULTS:
                    continue

                default_value = _DEFAULTS[key]
                try:
                    if isinstance(default_value, bool):
                        self._data[key] = value.lower() in {"true", "1", "yes", "on"}
                    elif isinstance(default_value, int):
                        self._data[key] = int(value)
                    elif isinstance(default_value, list):
                        self._data[key] = [item.strip() for item in value.split(",") if item.strip()]
                    else:
                        self._data[key] = value
                except (TypeError, ValueError):
                    self._data[key] = value

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if yaml is not None:
                with open(self.path, "w", encoding="utf-8") as handle:
                    yaml.safe_dump(self._data, handle, default_flow_style=False, sort_keys=True)
            else:
                self._save_fallback()
        except Exception as exc:
            print(f"[config] Failed to save {self.path}: {exc}")

    def _save_fallback(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            for key, value in sorted(self._data.items()):
                if isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                handle.write(f"{key}: {value}\n")
