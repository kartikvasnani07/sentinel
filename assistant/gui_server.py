import json
import os
import re
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from http.server import HTTPServer

import requests

from .config import AssistantConfig, VOICE_PRESETS, UI_MODES
from .intent_engine import IntentEngine
from .llm_engine import LLMEngine
from .memory import Memory
from .streaming_pipeline import StreamingPipeline
from .system_actions import SystemActions
from .tts_engine import TTSEngine


YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "proceed", "confirm", "do it"}
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "dont", "do not"}
CONFIRM_ACTIONS = {
    "delete_path",
    "shutdown_system",
    "restart_system",
    "sleep_system",
    "close_all_apps",
    "kill_process",
    "move_path",
    "rename_path",
    "duplicate_path",
}
WEATHER_KEYWORDS = {"weather", "temperature", "forecast", "humidity", "wind"}
WORD_RE = re.compile(r"[a-z0-9']+", re.I)


class AssistantRuntime:
    def __init__(self):
        self.config = AssistantConfig()
        self.llm = LLMEngine(online=True)
        self.llm.humor_level = int(self.config.get("humor_level", 50))
        self.intent_engine = IntentEngine(self.llm)
        self.system = SystemActions(base_dir=os.getcwd(), llm=self.llm, config=self.config)
        self.tts = TTSEngine(online=self.llm.online)
        preset = self.config.get("voice_preset")
        if preset:
            try:
                self.tts.apply_voice_preset(preset)
            except Exception:
                pass
        self.pipeline = StreamingPipeline(self.llm, self.tts)
        self.memory = Memory(max_turns=10)
        self.pending_action = None
        self.pending_prompt = ""
        self.pending_batch = []
        self.attachments = []
        self.rag_index = []
        self.model_preference = str(self.config.get("model_preference") or "auto").strip().lower()
        self.access_level = str(self.config.get("access_level") or "full").strip().lower()
        self.lock = threading.RLock()

    def _extract_leading_verb(self, text):
        lowered = str(text or "").strip().lower()
        if not lowered:
            return ""
        verbs = [
            "turn on",
            "turn off",
            "switch on",
            "switch off",
            "enable",
            "disable",
            "open",
            "close",
            "start",
            "stop",
            "play",
            "set",
            "list",
            "show",
            "delete",
            "remove",
            "erase",
            "create",
            "make",
            "move",
            "copy",
            "duplicate",
            "rename",
            "shutdown",
            "restart",
            "sleep",
            "read",
            "draw",
            "search",
            "find",
        ]
        for verb in verbs:
            if lowered.startswith(verb + " ") or lowered == verb:
                return verb
        return ""

    def _split_multi_commands(self, text):
        cleaned = " ".join(str(text or "").strip().split())
        if not cleaned:
            return []
        if not re.search(r"\b(and then|then|and|also)\b|[,;]", cleaned, flags=re.I):
            return [cleaned]
        parts = re.split(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b|\balso\b)\s*", cleaned, flags=re.I)
        parts = [part.strip() for part in parts if part and part.strip()]
        if len(parts) <= 1:
            return [cleaned]
        normalized = []
        last_verb = ""
        for idx, part in enumerate(parts):
            verb = self._extract_leading_verb(part)
            if idx > 0 and not verb and last_verb and not self._is_query_like(part):
                part = f"{last_verb} {part}".strip()
            if verb:
                last_verb = verb
            normalized.append(part)
        return normalized

    def _is_query_like(self, text):
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        starters = (
            "what",
            "how",
            "tell",
            "show",
            "give",
            "find",
            "search",
            "who",
            "where",
            "when",
            "why",
            "weather",
            "forecast",
            "news",
            "headlines",
            "trending",
        )
        return lowered.startswith(starters) or "weather" in lowered or "forecast" in lowered or "news" in lowered

    def _format_history(self):
        rows = self.memory.list_conversations()
        if not rows:
            return "No conversation history was found."
        lines = ["Conversation history:"]
        for row in rows[:30]:
            marker = "*" if row.get("is_current") else "-"
            lines.append(f"{marker} {row.get('id')} | {row.get('title')} ({row.get('message_count')} msgs)")
        return "\n".join(lines)

    def _apply_voice_preset(self, preset_name):
        key = str(preset_name or "").strip().lower()
        if not key:
            return "Please provide a voice preset name."
        preset = VOICE_PRESETS.get(key)
        if preset is None:
            available = ", ".join(sorted(VOICE_PRESETS.keys()))
            return f"Unknown preset '{preset_name}'. Available: {available}"
        self.config.set("voice_preset", key)
        return self.tts.apply_voice_preset(key)

    def _permission_allows(self, action):
        access = (self.access_level or "full").lower()
        if access == "full":
            return True

        read_actions = {
            "list_directory",
            "read_file",
            "get_setting_status",
            "list_history",
            "open_path",
            "open_application",
            "list_processes",
            "draw_file_tree",
            "get_news",
        }
        write_actions = {
            "create_file",
            "create_folder",
            "modify_file",
            "delete_path",
            "copy_path",
            "move_path",
            "rename_path",
            "duplicate_path",
            "change_directory",
            "project_code",
        }

        if access == "read":
            return action in read_actions
        if access == "write":
            return action in read_actions or action in write_actions
        return False

    def _summarize_attachments(self):
        if not self.attachments:
            return ""

        notes = []
        max_chars = 8000
        for raw in self.attachments:
            if max_chars <= 0:
                notes.append("... (attachment context truncated)")
                break
            path = Path(raw).expanduser()
            if not path.exists():
                notes.append(f"[Missing] {path}")
                continue
            if path.is_dir():
                self.system.session_context["last_project_root"] = str(path.resolve())
                try:
                    items = []
                    for item in path.rglob("*"):
                        if item.is_dir():
                            continue
                        items.append(str(item.relative_to(path)))
                        if len(items) >= 40:
                            break
                    preview = ", ".join(items)
                except Exception:
                    preview = ""
                note = f"[Folder] {path} (files: {preview})" if preview else f"[Folder] {path}"
                notes.append(note)
                max_chars -= len(note)
                continue

            parent_root = str(path.parent.resolve())
            self.system.session_context.setdefault("last_project_root", parent_root)
            if path.suffix.lower() in self.system.TEXT_EXTENSIONS:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    text = ""
                if len(text) > 2000:
                    text = text[:2000] + "\n... (truncated)"
                note = f"[File] {path}\n{text}"
            else:
                note = f"[File] {path} (binary or unsupported for preview)"
            notes.append(note)
            max_chars -= len(note)

        return "\n\n".join(notes)

    def _tokenize(self, text):
        return {match.group(0).lower() for match in WORD_RE.finditer(str(text or ""))}

    def _should_reset_context(self, text):
        last_user = self.memory.last("user")
        if not last_user:
            return False
        tokens_now = self._tokenize(text)
        tokens_prev = self._tokenize(last_user)
        if not tokens_now or not tokens_prev:
            return False
        overlap = len(tokens_now & tokens_prev) / max(1, len(tokens_now))
        if overlap >= 0.2:
            return False
        if any(token in tokens_now for token in {"this", "that", "it", "they", "those", "these", "previous"}):
            return False
        return True

    def _chunk_text(self, text, chunk_size=900, overlap=150):
        if not text:
            return []
        chunks = []
        start = 0
        text_length = len(text)
        while start < text_length:
            end = min(text_length, start + chunk_size)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - overlap
            if start < 0:
                start = 0
            if end == text_length:
                break
        return chunks

    def _build_rag_index(self):
        self.rag_index = []
        for raw in self.attachments:
            path = Path(raw).expanduser()
            if not path.exists() or path.is_dir():
                continue
            if path.suffix.lower() not in self.system.TEXT_EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for chunk in self._chunk_text(text):
                tokens = self._tokenize(chunk)
                if not tokens:
                    continue
                self.rag_index.append(
                    {
                        "source": str(path),
                        "chunk": chunk,
                        "tokens": tokens,
                    }
                )

    def _retrieve_rag_context(self, query, limit=3):
        if not self.rag_index:
            return ""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return ""
        scored = []
        for entry in self.rag_index:
            score = len(query_tokens & entry["tokens"])
            if score <= 0:
                continue
            scored.append((score, entry))
        if not scored:
            return ""
        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:limit]
        lines = []
        for _, entry in top:
            lines.append(f"Source: {entry['source']}\n{entry['chunk']}")
        return "\n\n".join(lines)

    def _generate_with_model(self, prompt, *, system_prompt=None):
        preference = (self.model_preference or "auto").lower()
        if preference == "groq":
            return str(self.llm.groq_generate(prompt, model=self.llm.groq_model, system_prompt=system_prompt))
        if preference == "openrouter":
            return str(self.llm.cloud_generate(prompt, model=self.llm.openrouter_model, system_prompt=system_prompt))
        if preference == "ollama":
            return str(self.llm.local_generate(prompt, model=self.llm.local_model, system_prompt=system_prompt))
        if preference == "groq-code":
            return str(self.llm.groq_generate(prompt, model=self.llm.groq_code_model, system_prompt=self.llm._build_code_system_prompt()))
        if preference == "openrouter-code":
            return str(self.llm.cloud_generate(prompt, model=self.llm.openrouter_code_model, system_prompt=self.llm._build_code_system_prompt()))
        if preference == "ollama-code":
            return str(self.llm.local_generate(prompt, model=self.llm.local_code_model, system_prompt=self.llm._build_code_system_prompt()))
        if system_prompt:
            if self.llm.groq_api_key:
                return str(self.llm.groq_generate(prompt, model=self.llm.groq_model, system_prompt=system_prompt))
            if self.llm.openrouter_api_key:
                return str(self.llm.cloud_generate(prompt, model=self.llm.openrouter_model, system_prompt=system_prompt))
            return str(self.llm.local_generate(prompt, model=self.llm.local_model, system_prompt=system_prompt))
        return str(self.llm.generate(prompt))

    def _is_weather_query(self, text):
        lowered = str(text or "").lower()
        return any(key in lowered for key in WEATHER_KEYWORDS)

    def _extract_weather_city(self, text):
        if not text:
            return None
        lowered = text.lower()
        match = re.search(r"\bweather\b.*?\bin\s+(.+)$", lowered)
        if not match:
            match = re.search(r"\btemperature\b.*?\bin\s+(.+)$", lowered)
        if not match:
            return None
        candidate = match.group(1)
        candidate = re.sub(r"\b(today|now|right now|currently|outside|please)\b", "", candidate, flags=re.I)
        candidate = re.sub(r"\b(like|forecast|forecasting)\b", "", candidate, flags=re.I)
        candidate = re.sub(r"\bweather\b", "", candidate, flags=re.I)
        candidate = candidate.strip(" ?.,")
        if not candidate:
            return None
        if candidate in {"my city", "my area", "here", "current location"}:
            return None
        return candidate

    def _get_ip_location(self):
        providers = [
            ("https://ipapi.co/json/", "ipapi"),
            ("https://ipinfo.io/json", "ipinfo"),
            ("http://ip-api.com/json", "ip-api"),
        ]
        headers = {"User-Agent": "assistant/1.0"}
        for url, provider in providers:
            try:
                resp = requests.get(url, timeout=6, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if provider == "ipinfo":
                    loc = str(data.get("loc") or "")
                    lat_str, lon_str = (loc.split(",", 1) + ["", ""])[:2]
                    return {
                        "city": data.get("city"),
                        "region": data.get("region"),
                        "lat": float(lat_str) if lat_str else None,
                        "lon": float(lon_str) if lon_str else None,
                    }
                if provider == "ip-api":
                    return {
                        "city": data.get("city"),
                        "region": data.get("regionName"),
                        "lat": data.get("lat"),
                        "lon": data.get("lon"),
                    }
                return {
                    "city": data.get("city"),
                    "region": data.get("region"),
                    "lat": data.get("latitude"),
                    "lon": data.get("longitude"),
                }
            except Exception:
                continue
        return {}

    def _normalize_weather_kind(self, main_label):
        label = str(main_label or "").strip().lower()
        if label in {"thunderstorm"}:
            return "storm"
        if label in {"drizzle", "rain", "showers"}:
            return "rain"
        if label in {"snow", "sleet"}:
            return "snow"
        if label in {"clear"}:
            return "sunny"
        if label in {"clouds", "overcast", "cloudy", "partly cloudy"}:
            return "cloudy"
        if label in {"mist", "fog", "haze"}:
            return "mist"
        return "cloudy"

    def _open_meteo_geocode(self, city):
        try:
            geo_resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "en", "format": "json"},
                timeout=8,
            )
            geo_resp.raise_for_status()
            results = geo_resp.json().get("results") or []
            if not results:
                return {}
            location = results[0]
            return {
                "city": location.get("name"),
                "region": location.get("admin1"),
                "country": location.get("country"),
                "lat": location.get("latitude"),
                "lon": location.get("longitude"),
            }
        except Exception:
            return {}

    def _open_meteo_forecast(self, lat, lon):
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,pressure_msl",
            "hourly": "temperature_2m,weather_code,precipitation",
            "forecast_days": 1,
            "timezone": "auto",
        }
        resp = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
        if resp.ok:
            return resp.json()
        legacy = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
                "hourly": "temperature_2m,weather_code,precipitation",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=10,
        )
        legacy.raise_for_status()
        return legacy.json()

    def _open_meteo_label(self, code):
        try:
            value = int(code)
        except (TypeError, ValueError):
            return "Cloudy", "Cloudy", "cloudy"
        if value == 0:
            return "Clear", "Clear sky", "sunny"
        if value in {1, 2}:
            return "Partly cloudy", "Mainly clear", "cloudy"
        if value == 3:
            return "Overcast", "Overcast", "cloudy"
        if value in {45, 48}:
            return "Fog", "Foggy", "mist"
        if value in {51, 53, 55}:
            return "Drizzle", "Light drizzle", "rain"
        if value in {56, 57}:
            return "Freezing drizzle", "Freezing drizzle", "rain"
        if value in {61, 63, 65}:
            return "Rain", "Rain", "rain"
        if value in {66, 67}:
            return "Freezing rain", "Freezing rain", "rain"
        if value in {71, 73, 75, 77}:
            return "Snow", "Snow", "snow"
        if value in {80, 81, 82}:
            return "Rain showers", "Rain showers", "rain"
        if value in {85, 86}:
            return "Snow showers", "Snow showers", "snow"
        if value in {95, 96, 99}:
            return "Thunderstorm", "Thunderstorm", "storm"
        return "Cloudy", "Cloudy", "cloudy"

    def _handle_weather_query_legacy(self, text):
        response, _ = self._handle_weather_query(text)
        return response
        city = self._extract_weather_city(text)
        location = {}
        if not location:
            location = self._get_ip_location()
        lat = location.get("lat")
        lon = location.get("lon")
        if lat is None or lon is None:
            return "I could not determine your location to fetch weather."
        try:
            weather = self._fetch_weather(lat, lon)
        except Exception:
            return "Weather lookup failed. Please try again in a moment."
        location_label = location.get("city") or city or "your area"
        region = location.get("region")
        if region and location_label and region not in location_label:
            location_label = f"{location_label}, {region}"
        temp = weather.get("temperature_c")
        humidity = weather.get("humidity")
        wind = weather.get("wind_kph")
        temp_str = f"{temp}°C" if temp is not None else "N/A"
        humidity_str = f"{humidity}%" if humidity is not None else "N/A"
        wind_str = f"{wind} km/h" if wind is not None else "N/A"
        link = f"https://weather.com/weather/today/l/{lat},{lon}"
        return (
            f"Current weather for {location_label}: Temperature {temp_str}, "
            f"Wind {wind_str}, Humidity {humidity_str}. More: {link}"
        )

    def _handle_weather_query(self, text):
        city = self._extract_weather_city(text)
        location = self._open_meteo_geocode(city) if city else {}
        if city and not location:
            cleaned = re.split(r"\b(?:like|please|today|now|currently)\b", city, flags=re.I)[0].strip(" ,.")
            if cleaned and cleaned != city:
                location = self._open_meteo_geocode(cleaned)
            if not location:
                simple = city.split(",")[0].strip()
                if simple and simple != city:
                    location = self._open_meteo_geocode(simple)
        if not location:
            ip_loc = self._get_ip_location()
            lat = ip_loc.get("lat")
            lon = ip_loc.get("lon")
            if lat is not None and lon is not None:
                location = dict(ip_loc)
            elif ip_loc.get("city"):
                location = self._open_meteo_geocode(ip_loc.get("city"))
        if not location:
            fallback = str(self.config.get("default_location") or "").strip()
            if fallback:
                location = self._open_meteo_geocode(fallback)

        lat = location.get("lat")
        lon = location.get("lon")
        if lat is None or lon is None:
            return ("I could not determine your location to fetch weather.", None)

        try:
            data = self._open_meteo_forecast(lat, lon)
        except Exception:
            return ("Weather lookup failed. Please try again in a moment.", None)

        current = data.get("current") or {}
        if not current and data.get("current_weather"):
            current_weather = data.get("current_weather") or {}
            current = {
                "temp": current_weather.get("temperature"),
                "weather_code": current_weather.get("weathercode"),
                "wind_speed": current_weather.get("windspeed"),
            }
        condition, description, kind = self._open_meteo_label(current.get("weather_code"))
        temp = current.get("temp") if current.get("temp") is not None else current.get("temperature_2m")
        feels_like = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        pressure = current.get("pressure_msl")
        wind_kph = current.get("wind_speed")

        location_label = location.get("city") or city or "your area"
        region = location.get("region") or location.get("country")
        if region and location_label and region not in location_label:
            location_label = f"{location_label}, {region}"

        hourly = []
        hourly_block = data.get("hourly") or {}
        times = hourly_block.get("time") or []
        temps = hourly_block.get("temperature_2m") or []
        codes = hourly_block.get("weather_code") or []
        precips = hourly_block.get("precipitation") or []
        for idx in range(min(8, len(times))):
            label = str(times[idx])
            if "T" in label:
                label = label.split("T", 1)[1]
            entry_condition, _, entry_kind = self._open_meteo_label(codes[idx] if idx < len(codes) else None)
            hourly.append(
                {
                    "time": label[:5],
                    "temp_c": temps[idx] if idx < len(temps) else None,
                    "condition": entry_condition,
                    "kind": entry_kind,
                    "precip_mm": precips[idx] if idx < len(precips) else None,
                }
            )

        link = "https://open-meteo.com/"

        temp_str = f"{temp:.1f}Â°C" if isinstance(temp, (int, float)) else "N/A"
        humidity_str = f"{humidity}%" if humidity is not None else "N/A"
        wind_str = f"{wind_kph} km/h" if wind_kph is not None else "N/A"
        response = (
            f"Current weather for {location_label}: Temperature {temp_str}, "
            f"Wind {wind_str}, Humidity {humidity_str}. More: {link}"
        )
        weather_payload = {
            "location": location_label,
            "lat": lat,
            "lon": lon,
            "condition": condition,
            "description": description or condition,
            "kind": kind,
            "temp_c": temp,
            "feels_like_c": feels_like,
            "humidity": humidity,
            "wind_kph": wind_kph,
            "pressure_hpa": pressure,
            "hourly": hourly,
            "link": link,
            "source": "Open-Meteo",
        }
        return response, weather_payload

    def _handle_weather_query_openweather(self, text):
        api_key = self._get_openweather_key()
        if not api_key:
            return (
                "OpenWeather API key not configured. Set openweather_api_key in config.yaml or OPENWEATHER_API_KEY and try again.",
                None,
            )

        city = self._extract_weather_city(text)
        location = self._openweather_geocode_city(city, api_key) if city else {}
        if city and not location:
            cleaned = re.split(r"\b(?:like|please|today|now|currently)\b", city, flags=re.I)[0].strip(" ,.")
            if cleaned and cleaned != city:
                location = self._openweather_geocode_city(cleaned, api_key)
            if not location:
                simple = city.split(",")[0].strip()
                if simple and simple != city:
                    location = self._openweather_geocode_city(simple, api_key)
        if not location:
            ip_loc = self._get_ip_location()
            lat = ip_loc.get("lat")
            lon = ip_loc.get("lon")
            if lat is not None and lon is not None:
                location = dict(ip_loc)
                if not location.get("city"):
                    location.update(self._openweather_reverse_geocode(lat, lon, api_key))
            elif ip_loc.get("city"):
                location = self._openweather_geocode_city(ip_loc.get("city"), api_key)
        if not location:
            fallback = str(self.config.get("default_location") or "").strip()
            if fallback:
                location = self._openweather_geocode_city(fallback, api_key)

        lat = location.get("lat")
        lon = location.get("lon")
        if lat is None or lon is None:
            return ("I could not determine your location to fetch weather.", None)

        try:
            data = self._openweather_onecall(lat, lon, api_key)
        except Exception:
            return ("Weather lookup failed. Please try again in a moment.", None)

        current = data.get("current") or {}
        weather_entry = (current.get("weather") or [{}])[0]
        condition = str(weather_entry.get("main") or "").strip()
        description = str(weather_entry.get("description") or "").strip()
        kind = self._normalize_weather_kind(condition)
        temp = current.get("temp")
        feels_like = current.get("feels_like")
        humidity = current.get("humidity")
        pressure = current.get("pressure")
        wind_data = {"speed": current.get("wind_speed")}
        wind_kph = None
        if wind_data.get("speed") is not None:
            try:
                wind_kph = round(float(wind_data.get("speed")) * 3.6, 1)
            except Exception:
                wind_kph = None

        location_label = location.get("city") or city or "your area"
        region = location.get("region") or location.get("country")
        if region and location_label and region not in location_label:
            location_label = f"{location_label}, {region}"

        hourly = []
        for entry in (data.get("hourly") or [])[:8]:
            entry_weather = (entry.get("weather") or [{}])[0]
            entry_condition = str(entry_weather.get("main") or "").strip()
            entry_kind = self._normalize_weather_kind(entry_condition)
            precip = entry.get("rain") or entry.get("snow")
            timestamp_label = ""
            if entry.get("dt"):
                try:
                    timestamp_label = time.strftime("%H:%M", time.localtime(float(entry.get("dt"))))
                except Exception:
                    timestamp_label = ""
            hourly.append(
                {
                    "time": timestamp_label,
                    "temp_c": entry.get("temp"),
                    "condition": entry_condition,
                    "kind": entry_kind,
                    "precip_mm": precip,
                }
            )

        link = f"https://openweathermap.org/weathermap?basemap=map&lat={lat}&lon={lon}&zoom=8"

        temp_str = f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "N/A"
        humidity_str = f"{humidity}%" if humidity is not None else "N/A"
        wind_str = f"{wind_kph} km/h" if wind_kph is not None else "N/A"
        response = (
            f"Current weather for {location_label}: Temperature {temp_str}, "
            f"Wind {wind_str}, Humidity {humidity_str}. More: {link}"
        )
        weather_payload = {
            "location": location_label,
            "lat": lat,
            "lon": lon,
            "condition": condition,
            "description": description or condition,
            "kind": kind,
            "temp_c": temp,
            "feels_like_c": feels_like,
            "humidity": humidity,
            "wind_kph": wind_kph,
            "pressure_hpa": pressure,
            "hourly": hourly,
            "link": link,
            "source": "OpenWeather",
        }
        return response, weather_payload

    def _should_plan(self, text, mode):
        if mode == "plan":
            return True
        lowered = str(text or "").lower()
        if "plan" in lowered or "steps" in lowered:
            return True
        return False

    def _generate_plan_response(self, prompt):
        system_prompt = (
            "You are the Planning Agent. Provide a clear step-by-step plan, followed by pros and cons. "
            "Do not execute any actions. Keep it concise and actionable."
        )
        return self._generate_with_model(prompt, system_prompt=system_prompt).strip()

    def _clean_response_text(self, text):
        cleaned = str(text or "")
        cleaned = cleaned.replace("**", "")
        cleaned = cleaned.replace("*", "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _handle_control_action(self, action, params):
        if action == "change_voice":
            return self._apply_voice_preset(params.get("preset"))
        if action == "change_assistant_name":
            name = params.get("assistant_name") or params.get("name") or params.get("target") or ""
            cleaned = " ".join(str(name).strip().split())
            if not cleaned:
                return "Please provide a name for the assistant."
            self.config.set("assistant_name", cleaned)
            return f"Assistant name set to {cleaned}."
        if action == "set_humor":
            level = params.get("level")
            if level is None:
                return "Please provide a humor level."
            self.config.set("humor_level", int(level))
            self.llm.humor_level = int(level)
            return f"Humor level set to {int(level)} percent."
        if action == "set_wake_sensitivity":
            percent = params.get("percent")
            if percent is None:
                return "Please provide a wake word sensitivity percentage."
            self.config.set("wake_sensitivity", int(percent))
            return f"Wake word sensitivity set to {int(percent)} percent."
        if action == "set_wave_display":
            enabled = bool(params.get("on", True))
            if enabled:
                self.config.update({"waves_enabled": True, "ui_mode": "waves"})
            else:
                self.config.set("waves_enabled", False)
            return "Wave interface updated."
        if action == "set_bubble_display":
            enabled = bool(params.get("on", True))
            if enabled:
                self.config.update({"waves_enabled": True, "ui_mode": "bubble"})
            else:
                self.config.set("waves_enabled", False)
            return "Bubble interface updated."
        if action == "set_interface_style":
            style = str(params.get("style") or "").strip().lower()
            if not style:
                return "Please provide an interface style."
            if style not in UI_MODES:
                style = "waves"
            self.config.update({"waves_enabled": True, "ui_mode": style})
            return f"Interface style set to {style}."
        if action == "clear_history":
            self.memory.clear()
            return "Conversation history cleared."
        if action == "list_history":
            return self._format_history()
        if action == "open_conversation":
            target = params.get("target") or params.get("conversation_id") or ""
            ok, message = self.memory.switch_to_conversation(target)
            return message if ok else message
        if action == "new_conversation":
            convo_id = self.memory.start_new_conversation()
            return f"Started new conversation {convo_id}."
        if action == "delete_conversation":
            convo_id = params.get("conversation_id") or ""
            if not convo_id:
                return "Please provide a conversation id."
            ok, message = self.memory.delete_conversation(convo_id)
            return message
        if action == "restart_setup":
            return "Setup restart is available in the console assistant."
        if action == "reset_password":
            return "Password reset is available in the console assistant."
        if action == "set_autostart":
            return "Autostart changes are available in the console assistant."
        if action == "set_wake_response":
            return "Wake response settings are available in the console assistant."
        return ""

    def apply_settings(self, payload):
        updates = {}
        name = payload.get("assistant_name")
        if isinstance(name, str):
            cleaned = " ".join(name.strip().split())
            if cleaned:
                self.config.set("assistant_name", cleaned)
                updates["assistant_name"] = cleaned
        voice = payload.get("voice_preset")
        if isinstance(voice, str) and voice.strip():
            try:
                self._apply_voice_preset(voice)
                updates["voice_preset"] = voice.strip().lower()
            except Exception:
                pass
        return updates

    def handle(self, text, confirm=None, model_preference=None, access_level=None, attachments=None, mode=None, allow_split=True):
        text = str(text or "").strip()
        if not text and not confirm:
            return {"response": "Please enter a command.", "needs_confirmation": False}

        with self.lock:
            if model_preference:
                self.model_preference = str(model_preference).strip().lower() or "auto"
                self.config.set("model_preference", self.model_preference)
            if access_level:
                self.access_level = str(access_level).strip().lower() or "full"
                self.config.set("access_level", self.access_level)
            if attachments:
                self.attachments = list(dict.fromkeys(str(item) for item in attachments if str(item).strip()))
                self._build_rag_index()

            if allow_split and confirm is None and self.pending_action is None:
                parts = self._split_multi_commands(text)
                if len(parts) > 1:
                    responses = []
                    for idx, part in enumerate(parts):
                        result = self.handle(
                            part,
                            confirm=None,
                            model_preference=None,
                            access_level=None,
                            attachments=None,
                            mode=mode,
                            allow_split=False,
                        )
                        responses.append(result.get("response", ""))
                        if result.get("needs_confirmation"):
                            self.pending_batch = parts[idx + 1 :]
                            return {
                                "response": "\n".join([r for r in responses if r]),
                                "needs_confirmation": True,
                            }
                    return {"response": "\n".join([r for r in responses if r]), "needs_confirmation": False}

            if self._is_weather_query(text):
                response, weather_payload = self._handle_weather_query(text)
                self.memory.add("user", text)
                self.memory.add("assistant", response)
                payload = {"response": response, "needs_confirmation": False}
                if weather_payload:
                    payload["weather"] = weather_payload
                return payload

            if self.pending_action is not None and confirm is not None:
                action, params = self.pending_action
                if confirm:
                    if not self._permission_allows(action):
                        self.pending_action = None
                        self.pending_prompt = ""
                        self.pending_batch = []
                        return {
                            "response": f"Blocked by {self.access_level} access mode. Update permissions to proceed.",
                            "needs_confirmation": False,
                        }
                    result = self.system.execute(action, params)
                    self.pending_action = None
                    self.pending_prompt = ""
                    if self.pending_batch:
                        remaining = list(self.pending_batch)
                        self.pending_batch = []
                        responses = [result]
                        for idx, part in enumerate(remaining):
                            follow = self.handle(
                                part,
                                confirm=None,
                                model_preference=None,
                                access_level=None,
                                attachments=None,
                                mode=mode,
                                allow_split=False,
                            )
                            responses.append(follow.get("response", ""))
                            if follow.get("needs_confirmation"):
                                self.pending_batch = remaining[idx + 1 :]
                                return {
                                    "response": "\n".join([r for r in responses if r]),
                                    "needs_confirmation": True,
                                }
                        return {"response": "\n".join([r for r in responses if r]), "needs_confirmation": False}
                    return {"response": result, "needs_confirmation": False}
                self.pending_action = None
                self.pending_prompt = ""
                self.pending_batch = []
                return {"response": "Cancelled.", "needs_confirmation": False}

            if (mode or "").lower() in {"respond", "chat", "generic"}:
                intent = "conversation"
                action = None
                params = {}
            else:
                intent_data = self.intent_engine.detect(text, context=self.system.session_context)
                intent = intent_data.get("intent", "conversation")
                action = intent_data.get("action")
                params = intent_data.get("parameters", {})

            if self._should_plan(text, (mode or "").lower()):
                context = "" if self._should_reset_context(text) else self.memory.get_context()
                attachment_context = self._summarize_attachments()
                rag_context = self._retrieve_rag_context(text)
                prompt_parts = []
                if attachment_context:
                    prompt_parts.append(f"Attached context:\n{attachment_context}")
                if rag_context:
                    prompt_parts.append(f"Retrieved context:\n{rag_context}")
                if context:
                    prompt_parts.append(context)
                prompt_parts.append(f"User: {text}\nAssistant:")
                prompt = "\n\n".join(prompt_parts).strip()
                try:
                    response = self._generate_plan_response(prompt)
                except Exception:
                    response = ""
                if not response:
                    response = "I could not generate a plan."
                response = self._clean_response_text(response)
                self.memory.add("user", text)
                self.memory.add("assistant", response)
                return {"response": response, "needs_confirmation": False, "mode": "plan"}

            if intent == "system_command" and action:
                control_result = self._handle_control_action(action, params)
                if control_result:
                    self.memory.add("user", text)
                    self.memory.add("assistant", control_result)
                    return {"response": control_result, "needs_confirmation": False}

                if not self._permission_allows(action):
                    self.memory.add("user", text)
                    self.memory.add("assistant", f"Blocked by {self.access_level} access mode. Update permissions to proceed.")
                    return {
                        "response": f"Blocked by {self.access_level} access mode. Update permissions to proceed.",
                        "needs_confirmation": False,
                    }

                if action in CONFIRM_ACTIONS:
                    target = self.system.describe_target(params)
                    prompt = f"Confirm {action.replace('_', ' ')} for {target}?"
                    self.pending_action = (action, params)
                    self.pending_prompt = prompt
                    self.memory.add("user", text)
                    self.memory.add("assistant", prompt)
                    return {"response": prompt, "needs_confirmation": True}

                result = self.system.execute(action, params)
                self.memory.add("user", text)
                self.memory.add("assistant", result)
                return {"response": result, "needs_confirmation": False}

            context = "" if self._should_reset_context(text) else self.memory.get_context()
            attachment_context = self._summarize_attachments()
            rag_context = self._retrieve_rag_context(text)
            prompt_parts = []
            if attachment_context:
                prompt_parts.append(f"Attached context:\n{attachment_context}")
            if rag_context:
                prompt_parts.append(f"Retrieved context:\n{rag_context}")
            if context:
                prompt_parts.append(context)
            prompt_parts.append(f"User: {text}\nAssistant:")
            prompt = "\n\n".join(prompt_parts).strip()

            try:
                response = self._generate_with_model(prompt).strip()
            except Exception:
                response = ""
            if not response:
                response = "I could not generate a response."
            response = self._clean_response_text(response)
            self.memory.add("user", text)
            self.memory.add("assistant", response)
            return {"response": response, "needs_confirmation": False, "model_used": self.model_preference}


class AssistantRequestHandler(BaseHTTPRequestHandler):
    runtime = AssistantRuntime()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            models = [
                {"id": "auto", "label": "Auto"},
            ]
            if self.runtime.llm.groq_api_key:
                models.append({"id": "groq", "label": f"Groq ({self.runtime.llm.groq_model})"})
                models.append({"id": "groq-code", "label": f"Groq Code ({self.runtime.llm.groq_code_model})"})
            if self.runtime.llm.openrouter_api_key:
                models.append({"id": "openrouter", "label": f"OpenRouter ({self.runtime.llm.openrouter_model})"})
                models.append({"id": "openrouter-code", "label": f"OpenRouter Code ({self.runtime.llm.openrouter_code_model})"})
            models.append({"id": "ollama", "label": f"Ollama ({self.runtime.llm.local_model})"})
            models.append({"id": "ollama-code", "label": f"Ollama Code ({self.runtime.llm.local_code_model})"})
            cloud_ready = bool(self.runtime.llm.groq_api_key or self.runtime.llm.openrouter_api_key)
            self._send_json(
                {
                    "status": "ok",
                    "cloud_ready": cloud_ready,
                    "model_preference": self.runtime.model_preference,
                    "access_level": self.runtime.access_level,
                    "models": models,
                }
            )
            return
        if path == "/api/voices":
            presets = []
            for key, value in VOICE_PRESETS.items():
                label = value.get("description") or key
                presets.append({"id": key, "label": label})
            presets.sort(key=lambda item: item["id"])
            self._send_json(
                {
                    "status": "ok",
                    "current": self.runtime.config.get("voice_preset"),
                    "presets": presets,
                }
            )
            return
        if path.startswith("/api/history/"):
            convo_id = path.split("/api/history/", 1)[1]
            convo = self.runtime.memory.get_conversation(convo_id)
            if convo is None:
                self._send_json({"error": "Conversation not found"}, status=404)
                return
            self._send_json({"status": "ok", "conversation": convo})
            return
        if path == "/api/history":
            rows = self.runtime.memory.list_conversations()
            self._send_json(
                {
                    "status": "ok",
                    "current": self.runtime.memory.current_conversation_id(),
                    "conversations": rows,
                }
            )
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/command", "/api/speak", "/api/settings", "/api/history/open", "/api/history/delete"}:
            self._send_json({"error": "Not found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"error": "Invalid JSON"}, status=400)
            return

        text = data.get("text")
        if path == "/api/settings":
            updates = self.runtime.apply_settings(data or {})
            self._send_json({"status": "ok", "updated": updates})
            return
        if path == "/api/history/open":
            convo_id = str(data.get("conversation_id") or "").strip()
            ok, message = self.runtime.memory.switch_to_conversation(convo_id)
            if not ok:
                self._send_json({"error": message}, status=404)
                return
            convo = self.runtime.memory.get_conversation(convo_id)
            self._send_json({"status": "ok", "conversation": convo})
            return
        if path == "/api/history/delete":
            convo_id = str(data.get("conversation_id") or "").strip()
            ok, message = self.runtime.memory.delete_conversation(convo_id)
            if not ok:
                self._send_json({"error": message}, status=404)
                return
            self._send_json({"status": "ok", "message": message})
            return
        if path == "/api/speak":
            if not text:
                self._send_json({"error": "No text provided."}, status=400)
                return
            try:
                self.runtime.tts.speak(str(text), replace=True, interrupt=True)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json({"status": "ok"})
            return
        confirm_flag = data.get("confirm")
        model_preference = data.get("model")
        access_level = data.get("access_level")
        mode = data.get("mode")
        attachments = data.get("attachments") or []
        confirm_value = None
        if confirm_flag is True:
            confirm_value = True
        elif confirm_flag is False:
            confirm_value = False
        else:
            lowered = str(text or "").strip().lower()
            if lowered in YES_WORDS:
                confirm_value = True
            elif lowered in NO_WORDS:
                confirm_value = False

        result = self.runtime.handle(
            text,
            confirm=confirm_value if confirm_value is not None else None,
            model_preference=model_preference,
            access_level=access_level,
            attachments=attachments,
            mode=mode,
        )
        log_payload = {
            "timestamp": time.time(),
            "request": {
                "text": text,
                "confirm": confirm_value,
                "model": model_preference,
                "access_level": access_level,
                "mode": mode,
                "attachments": attachments,
            },
            "response": result,
        }
        print(json.dumps(log_payload, ensure_ascii=False), flush=True)
        self._send_json(result)

    def log_message(self, fmt, *args):
        return


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run(host="127.0.0.1", port=8765):
    server = ThreadedHTTPServer((host, port), AssistantRequestHandler)
    print(f"[GUI] Assistant GUI server listening at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
