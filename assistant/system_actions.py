import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import time
import webbrowser
from ctypes import POINTER, cast
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

import keyboard
import requests

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except Exception:  # pragma: no cover - optional dependency
    CLSCTX_ALL = None
    AudioUtilities = None
    IAudioEndpointVolume = None

try:
    from yt_dlp import YoutubeDL
except Exception:  # pragma: no cover - optional dependency
    YoutubeDL = None

from .utils import draw_file_tree


@dataclass
class ProjectChange:
    path: str
    before: str
    after: str
    mode: str
    reason: str = ""
    before_exists: bool = False
    after_exists: bool = True


@dataclass
class ApplicationMatch:
    requested: str
    display_name: str
    kind: str
    command: str
    aliases: tuple[str, ...] = ()
    exact: bool = True


class SystemActions:
    READ_MAX_CHARS = 12000
    LIST_MAX_ITEMS = 200
    PATH_SCAN_LIMIT = 15000
    PROJECT_MAX_FILES = 80
    PROJECT_MAX_FILE_BYTES = 120_000
    TEXT_EXTENSIONS = {
        ".c",
        ".cc",
        ".cfg",
        ".conf",
        ".cpp",
        ".css",
        ".env",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mjs",
        ".py",
        ".rb",
        ".rs",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    APP_ALIASES = {
        "brave": ["brave.exe", "brave"],
        "brave browser": ["brave.exe", "brave"],
        "chrome": ["chrome.exe", "google-chrome", "chrome"],
        "google chrome": ["chrome.exe", "google-chrome", "chrome"],
        "edge": ["msedge.exe", "microsoft-edge", "edge"],
        "microsoft edge": ["msedge.exe", "microsoft-edge", "edge"],
        "firefox": ["firefox.exe", "firefox"],
        "blender": ["blender.exe", "blender"],
        "matlab": ["matlab.exe", "matlab"],
        "vlc": ["vlc.exe", "vlc"],
        "vlc media player": ["vlc.exe", "vlc"],
        "vscode": ["code.exe", "code"],
        "vs code": ["code.exe", "code"],
        "versus code": ["code.exe", "code"],
        "visual studio code": ["code.exe", "code"],
        "code": ["code.exe", "code"],
        "spotify": ["spotify.exe", "spotify"],
        "notepad": ["notepad.exe", "notepad"],
        "terminal": ["wt.exe", "powershell.exe", "cmd.exe"],
        "powershell": ["powershell.exe", "pwsh.exe"],
        "windows powershell": ["powershell.exe", "pwsh.exe"],
        "cmd": ["cmd.exe"],
        "camera": ["microsoft.windows.camera:"],
        "camera app": ["microsoft.windows.camera:"],
        "calculator": ["calc.exe"],
        "paint": ["mspaint.exe"],
        "explorer": ["explorer.exe"],
        "file explorer": ["explorer.exe"],
    }
    BROWSER_ALIASES = {
        "brave": "brave.exe",
        "brave browser": "brave.exe",
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "edge": "msedge.exe",
        "microsoft edge": "msedge.exe",
        "firefox": "firefox.exe",
    }
    SPECIAL_FOLDERS = {
        "desktop": "Desktop",
        "documents": "Documents",
        "document": "Documents",
        "downloads": "Downloads",
        "download": "Downloads",
        "pictures": "Pictures",
        "picture": "Pictures",
        "photos": "Pictures",
        "music": "Music",
        "videos": "Videos",
        "video": "Videos",
        "home": "",
    }
    PROJECT_SKIP_DIRS = {
        ".git",
        ".idea",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
    POWER_SAVER_GUID = "a1841308-3541-4fab-bc81-f71556f20b4a"
    BALANCED_POWER_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"
    DEFAULT_VOLUME_STEP = 10
    START_MENU_DIRS = (
        Path(os.getenv("ProgramData", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    )
    APP_NAME_REPLACEMENTS = (
        ("vs code", "visual studio code"),
        ("v s code", "visual studio code"),
        ("versus code", "visual studio code"),
        ("verses code", "visual studio code"),
        ("verse code", "visual studio code"),
        ("vscode", "visual studio code"),
        ("visual studio", "visual studio code"),
        ("vlc media player", "vlc"),
        ("camera app", "camera"),
        ("file explorer", "explorer"),
    )
    APP_CONTEXT_TERMS = (
        "youtube music",
        "vlc media player",
        "visual studio code",
        "versus code",
        "windows powershell",
        "file explorer",
        "brave browser",
        "google chrome",
        "microsoft edge",
        "camera app",
        "media player",
        "youtube",
        "spotify",
        "vlc",
        "brave",
        "chrome",
        "edge",
        "firefox",
        "camera",
        "blender",
        "matlab",
        "powershell",
        "terminal",
        "explorer",
        "browser",
        "app",
        "application",
    )
    PROCESS_HINTS = {
        "camera": {"names": {"windowscamera", "camera"}, "titles": {"camera"}},
        "brave": {"names": {"brave"}, "titles": {"brave"}},
        "brave browser": {"names": {"brave"}, "titles": {"brave"}},
        "visual studio code": {"names": {"code"}, "titles": {"visual studio code", "vs code"}},
        "vs code": {"names": {"code"}, "titles": {"visual studio code", "vs code"}},
        "versus code": {"names": {"code"}, "titles": {"visual studio code", "vs code"}},
        "vscode": {"names": {"code"}, "titles": {"visual studio code", "vs code"}},
        "vlc": {"names": {"vlc"}, "titles": {"vlc"}},
        "vlc media player": {"names": {"vlc"}, "titles": {"vlc"}},
        "blender": {"names": {"blender"}, "titles": {"blender"}},
        "matlab": {"names": {"matlab"}, "titles": {"matlab"}},
        "chrome": {"names": {"chrome"}, "titles": {"chrome"}},
        "edge": {"names": {"msedge"}, "titles": {"edge"}},
        "microsoft edge": {"names": {"msedge"}, "titles": {"edge"}},
        "firefox": {"names": {"firefox"}, "titles": {"firefox"}},
        "spotify": {"names": {"spotify"}, "titles": {"spotify"}},
        "explorer": {"names": {"explorer"}, "titles": {"file explorer", "explorer"}},
        "file explorer": {"names": {"explorer"}, "titles": {"file explorer", "explorer"}},
        "powershell": {"names": {"powershell", "pwsh"}, "titles": {"powershell", "windows powershell"}},
        "windows powershell": {"names": {"powershell", "pwsh"}, "titles": {"powershell", "windows powershell"}},
    }

    def __init__(self, base_dir=None, llm=None):
        self.base_dir = Path(base_dir or os.getcwd()).resolve()
        self.is_windows = platform.system().lower().startswith("win")
        self.llm = llm
        self.home = Path.home()
        self.standard_paths = {
            alias: (self.home / suffix).resolve() if suffix else self.home.resolve()
            for alias, suffix in self.SPECIAL_FOLDERS.items()
        }
        one_drive = os.getenv("OneDrive", "").strip()
        if one_drive:
            self.standard_paths["onedrive"] = Path(one_drive).resolve()
        self.session_context = {}
        self._start_apps_cache = None
        self._shortcut_apps_cache = None
        self.undo_stack = []
        self.redo_stack = []
        self.undo_limit = 50
        self.undo_cache_dir = self.home / ".assistant" / "undo_cache"
        self.undo_cache_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, action, parameters):
        action = str(action or "").strip().lower()
        params = self._prepare_params(parameters or {})

        handlers = {
            "shutdown_system": self._shutdown_system,
            "restart_system": self._restart_system,
            "sleep_system": self._sleep_system,
            "open_application": self._open_application,
            "open_path": self._open_path,
            "close_application": self._close_application,
            "undo_command": self._undo_last_action,
            "redo_command": self._redo_last_action,
            "list_directory": self._list_directory,
            "create_file": self._create_file,
            "create_folder": self._create_folder,
            "delete_path": self._delete_path,
            "modify_file": self._modify_file,
            "read_file": self._read_file,
            "change_directory": self._change_directory,
            "copy_path": self._copy_path,
            "move_path": self._move_path,
            "rename_path": self._rename_path,
            "duplicate_path": self._duplicate_path,
            "set_brightness": self._set_brightness,
            "set_volume": self._set_volume,
            "set_microphone": self._set_microphone,
            "get_setting_status": self._get_setting_status,
            "open_setting_panel": self._open_setting_panel,
            "set_wifi": self._set_wifi,
            "set_bluetooth": self._set_bluetooth,
            "set_airplane_mode": self._set_airplane_mode,
            "set_energy_saver": self._set_energy_saver,
            "set_night_light": self._set_night_light,
            "eject_drive": self._eject_drive,
            "run_as_admin": self._run_as_admin,
            "play_music": self._play_music,
            "draw_file_tree": self._draw_file_tree,
            "project_code": self._project_code,
        }

        handler = handlers.get(action)
        if handler is None:
            return f"Unknown system action: {action}"

        try:
            result = handler(params)
            self._update_session_context(action, params, result)
            return result
        except Exception as exc:
            return f"System action error: {exc}"

    def describe_target(self, params):
        data = self._prepare_params(params or {})
        path = self._resolve_path(data, expect_existing=False)
        if path is not None:
            return path.name
        if data.get("application"):
            return str(data["application"])
        if data.get("website"):
            return str(data["website"])
        return str(data.get("name") or "the requested item")

    def _spoken_target_name(self, params):
        data = self._prepare_params(params or {})
        if data.get("application"):
            return str(data["application"]).strip()
        if data.get("website"):
            website = str(data["website"]).strip()
            return website.split("/", 1)[0] if "/" in website else website
        target = self._resolve_path(data, expect_existing=False)
        if target is not None:
            return target.name or str(target)
        return str(data.get("name") or "it").strip()

    def _short_error_message(self, text):
        first_line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
        return first_line or str(text or "").strip()

    def _strip_usage_context(self, value):
        pattern = r"\s+\b(?:in|using|with|on)\b\s+(?=(?:the\s+)?(?:%s)\b)" % "|".join(
            re.escape(term) for term in self.APP_CONTEXT_TERMS
        )
        return re.split(pattern, str(value or "").strip(), maxsplit=1, flags=re.IGNORECASE)[0]

    def _clean_directory_reference(self, value):
        cleaned = str(value or "").strip().strip("\"'")
        if not cleaned:
            return ""
        lowered = self._normalize_query(cleaned)
        if lowered in {"current directory", "current folder", "this directory", "this folder", "here"}:
            return ""
        cleaned = self._strip_usage_context(cleaned)
        cleaned = re.sub(r"\b(?:contents|content)\s+(?:of|inside)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bfiles\s+(?:of|in|inside)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwhich\s+is\s+inside\b", "inside", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\binside\s+the\s+", "inside ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:inside|in|under|within)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("/folder", "").replace("/directory", "")
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:folder|directory|path)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        lowered = self._normalize_query(cleaned)
        if lowered in {"current directory", "current folder", "this directory", "this folder", "here"}:
            return ""
        return cleaned

    def _clean_target_reference(self, value):
        cleaned = str(value or "").strip().strip("\"'")
        if not cleaned:
            return ""
        cleaned = self._strip_usage_context(cleaned)
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^(?:file|folder|directory|path|app|application|program|software)\s+(?:named|called)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:named|called)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:which|that)\s+is$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"\b(?:which|that)$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(
            r"\b(?:file|folder|directory|path|app|application|program|software)\b$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip(" .")
        return cleaned

    def _spoken_path_label(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            target = self._path_from_fragment(params.get("path") or params.get("directory") or "")
        if target is None:
            name = self._clean_target_reference(params.get("name") or "")
            return name or "item"
        path = Path(target)
        if path.suffix:
            return path.stem
        return path.name or str(path)

    def _split_location_chain(self, value):
        cleaned = self._clean_directory_reference(value)
        if not cleaned:
            return []
        normalized = self._normalize_query(cleaned)
        normalized = re.sub(r"\bwhich\s+is\s+inside\b", "inside", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\b(?:inside|under|within)\b", "|", normalized, flags=re.IGNORECASE)
        parts = [part.strip(" .") for part in normalized.split("|") if part.strip(" .")]
        result = []
        for part in parts:
            part = re.sub(r"^(?:the|a|an)\s+", "", part, flags=re.IGNORECASE)
            part = re.sub(r"\b(?:folder|directory|path)\b$", "", part, flags=re.IGNORECASE).strip(" .")
            if part:
                result.append(part)
        return result

    def _resolve_directory_segment(self, segment, base=None):
        normalized = self._normalize_query(segment)
        if normalized in self.standard_paths:
            return self.standard_paths[normalized]
        search_root = Path(base) if base is not None else self.home
        if search_root.exists():
            candidate = self._find_closest_path(segment, search_root, source_hint="directory")
            if candidate is not None:
                return candidate
        if base is None:
            for root in self._default_search_roots():
                if not root.exists():
                    continue
                candidate = self._find_closest_path(segment, root, source_hint="directory")
                if candidate is not None:
                    return candidate
        return None

    def _resolve_nested_directory(self, fragment):
        segments = self._split_location_chain(fragment)
        if not segments:
            return None
        if len(segments) == 1:
            return self._resolve_directory_segment(segments[0], base=None)

        base = self._resolve_directory_segment(segments[-1], base=None)
        if base is None:
            return None
        for segment in reversed(segments[:-1]):
            next_path = self._resolve_directory_segment(segment, base=base)
            if next_path is None:
                guessed_name = re.sub(r"\b(?:folder|directory|path)\b$", "", segment, flags=re.IGNORECASE).strip(" .")
                if not guessed_name:
                    return None
                base = (Path(base) / guessed_name).resolve()
            else:
                base = next_path
        return base

    def _spoken_directory_label(self, params, result):
        first_line = self._short_error_message(result)
        if first_line.startswith("Contents of ") and first_line.endswith(":"):
            directory = self._path_from_fragment(first_line[len("Contents of ") : -1].strip())
            if directory is not None:
                return directory.name or "current directory"
        directory = self._path_from_fragment(params.get("directory") or "")
        if directory is None:
            return "current directory"
        return directory.name or "current directory"

    def build_voice_summary(self, action, params, result):
        text = str(result or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        first_line_lower = self._short_error_message(text).lower()
        if first_line_lower.startswith("system action error:") or first_line_lower.startswith("unknown system action:") or "failed" in first_line_lower or "not found" in first_line_lower:
            return self._short_error_message(text)
        spoken_target = self._spoken_target_name(params)
        if action == "open_application":
            if text.startswith("Captured ") or text.startswith("Started "):
                return text
            return f"Opened {spoken_target}."
        if action == "close_application":
            return f"Closed {spoken_target}."
        if action == "open_path":
            file_label = self._spoken_path_label(params)
            if params.get("application"):
                app_name = self._clean_target_reference(params.get("application") or spoken_target) or "the app"
                return f"{file_label} opened in {app_name}."
            return f"Opened {file_label}."
        if action == "create_file":
            created_path = self._resolve_path(params, expect_existing=False)
            created_name = created_path.name if created_path is not None else str(params.get("name") or self._spoken_path_label(params))
            return f"Created {created_name}."
        if action == "create_folder":
            return f"Created folder {self._spoken_path_label(params)}."
        if action == "delete_path":
            return f"Deleted {self._spoken_path_label(params)}."
        if action == "modify_file":
            return f"Updated {self._spoken_path_label(params)}."
        if action == "read_file":
            return f"Reading {self._spoken_path_label(params)}."
        if action == "play_music":
            if lowered.startswith("showing youtube music search results for "):
                query = text[len("Showing YouTube Music search results for ") :].strip().rstrip(".")
                return f"Showing YouTube Music results for {query}."
            if lowered.startswith("showing youtube search results for "):
                query = text[len("Showing YouTube search results for ") :].strip().rstrip(".")
                return f"Showing YouTube results for {query}."
            platform = str(params.get("platform") or "").strip().lower()
            if platform == "youtube_music":
                return f"Showing YouTube Music results for {params.get('song') or spoken_target}."
            if platform == "spotify":
                return f"Playing {params.get('song') or spoken_target} on Spotify."
            return f"Showing YouTube results for {params.get('song') or spoken_target}."
        if action in {"set_volume", "set_microphone", "set_brightness", "set_wifi", "set_bluetooth", "set_airplane_mode", "set_energy_saver", "set_night_light"}:
            return self._short_error_message(text) if "error" in lowered else text
        if action == "list_directory":
            return f"Listing the contents of {self._spoken_directory_label(params, result)}."
        if action == "draw_file_tree":
            return f"Showing the file tree for {self._spoken_directory_label(params, result)}."
        if action == "project_code":
            return "Project changes are ready."
        return text

    def clear_context(self):
        self.session_context.clear()

    def resolve_application_request(self, params):
        data = self._prepare_params(params or {})
        requested = str(data.get("application") or data.get("name") or "").strip()
        if not requested:
            return {"status": "missing", "requested": ""}

        exact_match = self._find_application_match(requested, exact_only=True)
        if exact_match:
            return {
                "status": "exact",
                "requested": requested,
                "display_name": exact_match.display_name,
                "kind": exact_match.kind,
                "command": exact_match.command,
                "aliases": list(exact_match.aliases),
            }

        fuzzy_match = self._find_application_match(requested, exact_only=False)
        if fuzzy_match:
            return {
                "status": "needs_confirmation",
                "requested": requested,
                "display_name": fuzzy_match.display_name,
                "kind": fuzzy_match.kind,
                "command": fuzzy_match.command,
                "aliases": list(fuzzy_match.aliases),
            }

        return {"status": "missing", "requested": requested}

    def _default_search_roots(self):
        roots = [Path.cwd(), *self.standard_paths.values()]
        cwd = Path.cwd().resolve()
        if cwd.anchor:
            roots.append(Path(cwd.anchor))
        unique = []
        seen = set()
        for root in roots:
            candidate = Path(root)
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def find_path_candidates(self, name, directory=None, source_hint=None, max_results=10):
        target_name = self._normalize_spoken_filename(name)
        target_lower = target_name.lower()
        target_stem = Path(target_name).stem.lower()
        roots = [self._path_from_fragment(directory)] if directory is not None else self._default_search_roots()
        candidates = []
        seen = set()
        for root in roots:
            if root is None or not Path(root).exists():
                continue
            for candidate in self._collect_search_candidates(Path(root), source_hint=source_hint):
                key = str(candidate).lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)

        exact = [
            candidate
            for candidate in candidates
            if candidate.name.lower() == target_lower or candidate.stem.lower() == target_stem
        ]
        if exact:
            return sorted(exact, key=lambda item: (item.name.lower() != target_lower, len(str(item))))[:max_results]

        name_map = {candidate.name.lower(): candidate for candidate in candidates}
        stem_map = {candidate.stem.lower(): candidate for candidate in candidates if candidate.is_file()}
        close_keys = get_close_matches(target_lower, list(name_map.keys()) + list(stem_map.keys()), n=max_results, cutoff=0.55)
        matches = []
        for key in close_keys:
            match = name_map.get(key) or stem_map.get(key)
            if match is not None and match not in matches:
                matches.append(match)
        if not matches:
            partial = [
                candidate
                for candidate in candidates
                if target_lower in candidate.name.lower() or candidate.name.lower() in target_lower
            ]
            partial = sorted(partial, key=lambda item: (len(item.name), len(str(item))))
            matches.extend(partial[:max_results])
        return matches[:max_results]

    def _prepare_params(self, params):
        data = dict(params)
        raw_text = str(data.get("raw_text") or "").strip()
        normalized = self._normalize_query(raw_text)

        if not data.get("website") and raw_text:
            website = self._extract_website_from_text(raw_text)
            if website:
                data["website"] = website

        if not data.get("application") and raw_text:
            application = self._extract_application_from_text(raw_text)
            if application:
                data["application"] = application

        if not data.get("name") and raw_text:
            name = self._extract_target_name(raw_text)
            if name:
                data["name"] = name

        if "directory" not in data and raw_text:
            directory = self._extract_directory_from_text(raw_text)
            if directory is not None:
                data["directory"] = directory

        if not data.get("content") and raw_text:
            literal = self._extract_literal_content(raw_text)
            if literal:
                data["content"] = literal

        if not data.get("name") and self._refers_to_previous_target(normalized):
            previous = self.session_context.get("last_path")
            if previous:
                data["path"] = previous
                data["name"] = Path(previous).name

        if not data.get("application") and self._refers_to_previous_target(normalized):
            previous_app = self.session_context.get("last_application")
            if previous_app:
                data["application"] = previous_app

        return data

    def _normalize_query(self, text):
        value = str(text or "").strip().lower()
        value = re.sub(
            r"\bdot\s+((?:[a-z0-9]\s+){1,7}[a-z0-9])\b",
            lambda match: "dot " + match.group(1).replace(" ", ""),
            value,
        )
        replacements = (
            ("dot slash", "./"),
            ("forward slash", "/"),
            ("back slash", "\\"),
            (" slash ", "/"),
            (" dot ", "."),
            (" underscore ", "_"),
            (" hyphen ", "-"),
            (" dash ", "-"),
            ("recyclebin", "recycle bin"),
            ("wi fi", "wifi"),
            ("blue tooth", "bluetooth"),
            ("air plane", "airplane"),
            ("flight mode", "airplane mode"),
            ("enery saver", "energy saver"),
            ("battery saver", "energy saver"),
            ("night ligh", "night light"),
            ("one drive", "onedrive"),
            ("vs code", "visual studio code"),
            ("versus code", "visual studio code"),
            ("verses code", "visual studio code"),
            ("verse code", "visual studio code"),
            ("vscode", "visual studio code"),
            ("c plus plus", "cpp"),
            ("c v two", "cv2"),
            ("c v 2", "cv2"),
            ("cv two", "cv2"),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        value = re.sub(r"\bunderscore\b", "_", value, flags=re.IGNORECASE)
        value = re.sub(r"\b(?:hyphen|dash)\b", "-", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*([_.-])\s*", r"\1", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _normalize_app_name(self, text):
        value = self._normalize_query(text)
        for source, target in self.APP_NAME_REPLACEMENTS:
            value = value.replace(source, target)
        value = re.sub(
            r"\b(?:open|run|launch|start|show|use|using|in|with|the|app|application|program|software)\b",
            " ",
            value,
        )
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _get_start_apps(self):
        if not self.is_windows:
            return []
        if self._start_apps_cache is not None:
            return self._start_apps_cache
        try:
            raw = self._powershell("Get-StartApps | Sort-Object Name | Select-Object Name, AppID | ConvertTo-Json -Depth 3", timeout=20)
            data = json.loads(raw) if raw else []
            if isinstance(data, dict):
                data = [data]
        except Exception:
            data = []
        self._start_apps_cache = [item for item in data if isinstance(item, dict) and item.get("Name") and item.get("AppID")]
        return self._start_apps_cache

    def _get_shortcut_apps(self):
        if self._shortcut_apps_cache is not None:
            return self._shortcut_apps_cache
        entries = []
        seen = set()
        for root in self.START_MENU_DIRS:
            if not root.exists():
                continue
            for shortcut in root.rglob("*.lnk"):
                key = str(shortcut).lower()
                if key in seen:
                    continue
                seen.add(key)
                entries.append({"Name": shortcut.stem, "Path": str(shortcut)})
        self._shortcut_apps_cache = entries
        return self._shortcut_apps_cache

    def _candidate_to_app_match(self, alias, candidate):
        display_name = alias.title()
        aliases = tuple({self._normalize_app_name(alias), self._normalize_app_name(display_name), alias.lower()})
        if str(candidate).endswith(":"):
            return ApplicationMatch(requested=alias, display_name=display_name, kind="uri", command=str(candidate), aliases=aliases)
        executable = self._find_executable(candidate)
        if executable:
            return ApplicationMatch(requested=alias, display_name=display_name, kind="executable", command=str(executable), aliases=aliases)
        return None

    def _application_catalog(self):
        matches = []
        seen = set()
        for alias, candidates in self.APP_ALIASES.items():
            for candidate in candidates:
                match = self._candidate_to_app_match(alias, candidate)
                if match is None:
                    continue
                key = (match.kind, match.command.lower())
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
                break

        for item in self._get_start_apps():
            display_name = str(item.get("Name") or "").strip()
            app_id = str(item.get("AppID") or "").strip()
            if not display_name or not app_id:
                continue
            executable = None
            if app_id.lower().endswith(".exe"):
                executable = self._find_executable(Path(app_id).name)
            kind = "executable" if executable else "appid"
            command = executable or app_id
            key = (kind, command.lower())
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                ApplicationMatch(
                    requested=display_name,
                    display_name=display_name,
                    kind=kind,
                    command=command,
                    aliases=(self._normalize_app_name(display_name), display_name.lower()),
                )
            )

        return matches

    def _resolve_alias_application(self, alias):
        for candidate in self.APP_ALIASES.get(alias, []):
            match = self._candidate_to_app_match(alias, candidate)
            if match is not None:
                return match
        return None

    def _start_app_match(self, display_name, app_id, requested, *, exact):
        return ApplicationMatch(
            requested=requested,
            display_name=display_name,
            kind="appid",
            command=app_id,
            aliases=(self._normalize_app_name(display_name), display_name.lower()),
            exact=exact,
        )

    def _token_overlap_ratio(self, left, right):
        left_tokens = set(re.findall(r"[a-z0-9]+", str(left or "")))
        right_tokens = set(re.findall(r"[a-z0-9]+", str(right or "")))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / float(len(left_tokens | right_tokens))

    def _best_similarity_key(self, requested, candidates):
        normalized_requested = str(requested or "").strip()
        if not normalized_requested:
            return ""
        best_key = ""
        best_score = 0.0
        for candidate in candidates:
            key = str(candidate or "").strip()
            if not key:
                continue
            ratio = difflib.SequenceMatcher(None, normalized_requested, key).ratio()
            overlap = self._token_overlap_ratio(normalized_requested, key)
            contains = 1.0 if (normalized_requested in key or key in normalized_requested) else 0.0
            score = (0.65 * ratio) + (0.30 * overlap) + (0.05 * contains)
            if score > best_score:
                best_score = score
                best_key = key
        return best_key if best_score >= 0.58 else ""

    def _find_application_match(self, requested, exact_only=False):
        normalized_requested = self._normalize_app_name(requested)
        if not normalized_requested:
            return None
        alias_lookup = {}
        for alias in self.APP_ALIASES:
            normalized_alias = self._normalize_app_name(alias)
            current = alias_lookup.get(normalized_alias)
            if current is None or len(alias) > len(current):
                alias_lookup[normalized_alias] = alias
            current_lower = alias_lookup.get(alias.lower())
            if current_lower is None or len(alias) > len(current_lower):
                alias_lookup[alias.lower()] = alias

        alias = alias_lookup.get(normalized_requested)
        if alias:
            match = self._resolve_alias_application(alias)
            if match is not None:
                return ApplicationMatch(
                    requested=requested,
                    display_name=match.display_name,
                    kind=match.kind,
                    command=match.command,
                    aliases=match.aliases,
                    exact=True,
                )

        start_apps = self._get_start_apps()
        normalized_start_apps = {}
        exact_start = None
        for item in start_apps:
            display_name = str(item.get("Name") or "").strip()
            app_id = str(item.get("AppID") or "").strip()
            if not display_name or not app_id:
                continue
            normalized_name = self._normalize_app_name(display_name)
            normalized_start_apps.setdefault(normalized_name, (display_name, app_id))
            if normalized_name == normalized_requested:
                exact_start = (display_name, app_id)
        if exact_start is not None:
            return self._start_app_match(exact_start[0], exact_start[1], requested, exact=True)

        if exact_only:
            return None

        close_alias = get_close_matches(normalized_requested, list(alias_lookup.keys()), n=1, cutoff=0.62)
        if close_alias:
            alias = alias_lookup[close_alias[0]]
            match = self._resolve_alias_application(alias)
            if match is not None:
                return ApplicationMatch(
                    requested=requested,
                    display_name=match.display_name,
                    kind=match.kind,
                    command=match.command,
                    aliases=match.aliases,
                    exact=False,
                )

        close_start = get_close_matches(normalized_requested, list(normalized_start_apps.keys()), n=1, cutoff=0.64)
        if close_start:
            display_name, app_id = normalized_start_apps[close_start[0]]
            return self._start_app_match(display_name, app_id, requested, exact=False)

        best_alias_key = self._best_similarity_key(normalized_requested, alias_lookup.keys())
        if best_alias_key:
            alias = alias_lookup.get(best_alias_key)
            if alias:
                match = self._resolve_alias_application(alias)
                if match is not None:
                    return ApplicationMatch(
                        requested=requested,
                        display_name=match.display_name,
                        kind=match.kind,
                        command=match.command,
                        aliases=match.aliases,
                        exact=False,
                    )

        best_start_key = self._best_similarity_key(normalized_requested, normalized_start_apps.keys())
        if best_start_key:
            display_name, app_id = normalized_start_apps[best_start_key]
            return self._start_app_match(display_name, app_id, requested, exact=False)
        return None

    def _launch_application_match(self, match, arguments=None):
        args = [str(item) for item in (arguments or []) if str(item).strip()]
        if match.kind == "uri":
            os.startfile(match.command)
            return
        if match.kind == "shortcut":
            if args:
                raise RuntimeError(f"{match.display_name} does not support file arguments through its shortcut.")
            os.startfile(match.command)
            return
        if match.kind == "appid":
            if args:
                raise RuntimeError(f"{match.display_name} cannot open files directly through its Start menu AppID.")
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{match.command}"])
            return
        subprocess.Popen([match.command, *args])

    def _process_hints_for_application(self, requested, match=None):
        normalized_requested = self._normalize_app_name(requested or (match.display_name if match else ""))
        names = set()
        titles = set()
        if match is not None and match.kind == "executable":
            names.add(Path(match.command).stem.lower())
        hint_key = normalized_requested
        if hint_key in self.PROCESS_HINTS:
            names.update(self.PROCESS_HINTS[hint_key]["names"])
            titles.update(self.PROCESS_HINTS[hint_key]["titles"])
        names.update(part for part in re.split(r"[^a-z0-9]+", normalized_requested) if len(part) >= 3)
        titles.add(normalized_requested)
        return sorted(names), sorted(titles)

    def _query_matching_processes(self, names=None, titles=None):
        if not self.is_windows:
            return {"count": 0, "names": []}
        names_json = self._ps_quote(json.dumps(list(names or [])))
        titles_json = self._ps_quote(json.dumps(list(titles or [])))
        script = f"""
$names = ConvertFrom-Json {names_json}
$titles = ConvertFrom-Json {titles_json}
$matches = @(Get-Process | Where-Object {{
    $proc = $_.ProcessName.ToLower()
    $title = if ($_.MainWindowTitle) {{ $_.MainWindowTitle.ToLower() }} else {{ '' }}
    (($names | Where-Object {{ $_ -and ($proc -eq $_ -or $proc -like ('*' + $_ + '*')) }}).Count -gt 0) -or
    (($titles | Where-Object {{ $_ -and $title -and $title -like ('*' + $_ + '*') }}).Count -gt 0)
}})
@{{ count = $matches.Count; names = @($matches | Select-Object -ExpandProperty ProcessName -Unique) }} | ConvertTo-Json -Compress
""".strip()
        payload = self._powershell(script, timeout=20)
        data = json.loads(payload)
        return data if isinstance(data, dict) else {"count": 0, "names": []}

    def _stop_matching_processes(self, names=None, titles=None):
        if not self.is_windows:
            raise RuntimeError("Application closing is only implemented on Windows in this build.")
        names_json = self._ps_quote(json.dumps(list(names or [])))
        titles_json = self._ps_quote(json.dumps(list(titles or [])))
        script = f"""
$names = ConvertFrom-Json {names_json}
$titles = ConvertFrom-Json {titles_json}
$matches = @(Get-Process | Where-Object {{
    $proc = $_.ProcessName.ToLower()
    $title = if ($_.MainWindowTitle) {{ $_.MainWindowTitle.ToLower() }} else {{ '' }}
    (($names | Where-Object {{ $_ -and ($proc -eq $_ -or $proc -like ('*' + $_ + '*')) }}).Count -gt 0) -or
    (($titles | Where-Object {{ $_ -and $title -and $title -like ('*' + $_ + '*') }}).Count -gt 0)
}} | Sort-Object Id -Unique)
if (-not $matches) {{
    throw 'No running process matched.'
}}
$namesOut = @($matches | Select-Object -ExpandProperty ProcessName -Unique)
$matches | Stop-Process -Force
@{{ count = $matches.Count; names = $namesOut }} | ConvertTo-Json -Compress
""".strip()
        payload = self._powershell(script, timeout=20)
        data = json.loads(payload)
        return data if isinstance(data, dict) else {"count": 0, "names": []}

    def _trigger_camera_shortcut(self, mode, *, warmup_seconds):
        time.sleep(max(0.0, warmup_seconds))
        try:
            if mode == "video":
                keyboard.send("ctrl+r")
            else:
                keyboard.send("space")
        except Exception as exc:
            raise RuntimeError(f"Camera shortcut failed: {exc}") from exc

    def _youtube_oembed_available(self, video_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(video_id or "").strip()):
            return False
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            response = requests.get(
                url,
                timeout=4.5,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                    )
                },
            )
            return response.status_code == 200
        except Exception:
            return False

    def _score_youtube_candidate(self, query, title, uploader, url, position):
        query_tokens = {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 2}
        title_tokens = {token for token in re.findall(r"[a-z0-9]+", str(title or "").lower()) if len(token) >= 2}
        uploader_tokens = {token for token in re.findall(r"[a-z0-9]+", str(uploader or "").lower()) if len(token) >= 2}
        score = float(len(query_tokens & title_tokens)) + (1.35 * float(len(query_tokens & uploader_tokens)))
        lowered_title = str(title or "").lower()
        if "official" in lowered_title:
            score += 1.0
        if "audio" in lowered_title and "audio" in query.lower():
            score += 0.45
        if "lyrics" in lowered_title and "lyrics" not in query.lower():
            score -= 0.4
        if "live" in lowered_title and "live" not in query.lower():
            score -= 0.35
        if "shorts" in str(url or "").lower():
            score -= 0.5
        score += max(0.0, 0.35 - (position * 0.03))
        return score

    def _find_youtube_video_url(self, song, *, music=False):
        query = self._normalize_media_query(song)
        if not query:
            return ""
        candidates = []
        if YoutubeDL is not None:
            try:
                ydl = YoutubeDL(
                    {
                        "quiet": True,
                        "skip_download": True,
                        "extract_flat": "in_playlist",
                        "default_search": "ytsearch",
                        "noplaylist": True,
                    }
                )
                payload = ydl.extract_info(f"ytsearch8:{query}", download=False) or {}
                entries = payload.get("entries") or []
                for index, entry in enumerate(entries):
                    title = str(entry.get("title") or "").strip()
                    uploader = str(entry.get("uploader") or entry.get("channel") or "").strip()
                    video_id = str(entry.get("id") or "").strip()
                    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                        continue
                    url = str(entry.get("webpage_url") or "").strip() or f"https://www.youtube.com/watch?v={video_id}"
                    if "youtube.com" not in url and "youtu.be" not in url:
                        continue
                    score = self._score_youtube_candidate(query, title, uploader, url, index)
                    candidates.append(
                        {
                            "score": score,
                            "url": url,
                            "video_id": video_id,
                        }
                    )
            except Exception:
                pass

        if not candidates:
            search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            try:
                response = requests.get(
                    search_url,
                    timeout=5.0,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                        )
                    },
                )
                ids = []
                for match in re.findall(r"watch\?v=([A-Za-z0-9_-]{11})", response.text):
                    if match not in ids:
                        ids.append(match)
                    if len(ids) >= 8:
                        break
                for index, video_id in enumerate(ids):
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    candidates.append(
                        {
                            "score": 2.0 - (index * 0.1),
                            "url": url,
                            "video_id": video_id,
                        }
                    )
            except Exception:
                pass

        if not candidates:
            return ""

        candidates.sort(key=lambda item: item["score"], reverse=True)
        for candidate in candidates[:10]:
            video_id = candidate["video_id"]
            if not self._youtube_oembed_available(video_id):
                continue
            if music:
                return f"https://music.youtube.com/watch?v={video_id}&autoplay=1"
            return f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
        return ""

    def _normalize_media_query(self, text):
        value = self._normalize_query(text)
        value = value.replace("three blue one brown", "3blue1brown")
        value = re.sub(r"\b(?:play|open|run|start)\b", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\b(?:in|on|using|with)\s+(youtube music|youtube|spotify)\b", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\b(?:song|music)\b$", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip(" .")
        return value

    def _extract_media_search_query(self, requested, raw_text):
        base = str(requested or "").strip()
        if not base:
            base = str(raw_text or "").strip()
        text = self._normalize_query(base)
        text = text.replace("three blue one brown", "3blue1brown")
        text = re.sub(r"\b(?:play|open|run|start|search|find)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:in|on|using|with)\s+(youtube music|youtube|spotify)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:please|can you|could you|would you|for me|right now|now)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:show me|help me)\b", " ", text, flags=re.IGNORECASE)
        tokens = re.findall(r"[a-z0-9@#'+_.-]+", text)
        stop_tokens = {
            "the",
            "a",
            "an",
            "song",
            "music",
            "video",
            "clip",
            "track",
            "please",
            "play",
            "open",
            "run",
            "start",
            "search",
            "find",
            "in",
            "on",
            "using",
            "with",
            "youtube",
            "youtube_music",
            "spotify",
        }
        filtered = [token for token in tokens if token not in stop_tokens and len(token) >= 2]
        query = " ".join(filtered[:14]).strip()
        if query:
            return query
        fallback = self._normalize_media_query(requested or raw_text)
        return fallback or "music"

    def _refers_to_previous_target(self, normalized):
        tokens = set(re.findall(r"[a-z0-9_./\\-]+", normalized))
        return bool(tokens & {"it", "that", "there", "them", "this"})

    def _extract_application_from_text(self, text):
        lowered = self._normalize_query(text)
        for alias in sorted(self.APP_ALIASES, key=len, reverse=True):
            if alias in lowered:
                return alias
        normalized = self._normalize_app_name(text)
        if normalized in self.APP_ALIASES:
            return normalized
        return None

    def _extract_website_from_text(self, text):
        lowered = self._normalize_query(text)
        domain_match = re.search(
            r"\b((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s]*)?)\b",
            lowered,
        )
        if domain_match:
            return domain_match.group(1)
        if "youtube" in lowered or "github" in lowered or "spotify" in lowered:
            return lowered
        return None

    def _extract_directory_from_text(self, text):
        tree_match = re.search(
            r"\b(?:draw|show|display)\s+(?:the\s+)?(?:file|folder|directory|system)?\s*(?:tree|structure)\s+(?:of|for|inside)\s+(?:the\s+)?(.+)$",
            text,
            flags=re.IGNORECASE,
        ) or re.search(
            r"\b(?:file|folder|directory|system)\s+(?:tree|structure)\s+(?:of|for|inside)\s+(?:the\s+)?(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if tree_match:
            directory = self._clean_directory_reference(tree_match.group(1).strip(" ."))
            return directory or ""

        list_match = re.search(
            r"\b(?:list|show|display)\s+(?:the\s+)?(?:contents|files)?(?:\s+(?:of|inside))?\s+(?:the\s+)?(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if list_match:
            directory = self._clean_directory_reference(list_match.group(1).strip(" ."))
            return directory or ""

        match = re.search(
            r"\b(?:in|inside|under|at|from)\s+(.+?)(?=\s+\b(?:named|called|with|and|that|which|into|using|on|open|launch|run|start|read|delete|create|make|copy|move|rename|duplicate|list|show|display|update|modify|change|edit|fix|rewrite)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        directory = self._clean_directory_reference(match.group(1).strip(" ."))
        lowered = directory.lower()
        if lowered in {"text mode", "voice mode"} or lowered.startswith(("which ", "that ", "where ")):
            return None
        if lowered in {"which", "that", "where", "file", "folder", "directory", "path"}:
            return None
        if (" file" in lowered or lowered.endswith("file")) and (
            re.search(r"\.[a-z0-9]{1,8}\b", lowered) or " dot " in lowered
        ):
            return None
        language_tokens = {
            "python",
            "py",
            "cpp",
            "c++",
            "java",
            "javascript",
            "typescript",
            "text",
            "json",
            "yaml",
            "markdown",
        }
        if lowered in language_tokens:
            return None
        if any(lowered.startswith(f"{token} ") for token in language_tokens):
            if re.search(r"\bin\s+(?:python|py|cpp|c\+\+|java|javascript|typescript|text|json|yaml|markdown)\b", text, flags=re.IGNORECASE):
                return None
        return directory

    def _extract_target_name(self, text):
        quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
        if quoted:
            return quoted[0].strip()

        inside_named = re.search(
            r"\binside\s+(?:the\s+)?file\s+(?:named|called)\s+(.+?)(?=\s*(?:,|;|$|\b(?:to|with|that|which|and|where|update|modify|change|edit)\b))",
            text,
            flags=re.IGNORECASE,
        )
        if inside_named:
            value = self._clean_target_reference(inside_named.group(1))
            if value:
                return value

        inside_file = re.search(
            r"\b(?:inside|in)\s+(.+?)\s+\bfile\b",
            text,
            flags=re.IGNORECASE,
        )
        if inside_file:
            value = self._clean_target_reference(inside_file.group(1))
            if value:
                return value

        explicit = re.search(
            r"\b(?:file|folder|directory|path)\s+(?:named|called)\s+(.+?)(?=\s*(?:,|;|$|\b(?:in|inside|under|at|from|to|with|using|and|on|update|modify|change|edit)\b))",
            text,
            flags=re.IGNORECASE,
        )
        if explicit:
            raw = explicit.group(1).strip(" .")
            raw = re.split(r"\s*,\s*", raw, maxsplit=1)[0]
            value = self._clean_target_reference(raw)
            return value or None

        match = re.search(
            r"\b(?:create|make|generate|write|open|launch|run|start|show|delete|remove|erase|copy|move|rename|duplicate|read|list|display|play)\s+(.+?)(?=\s+\b(?:in|inside|under|at|with|using|from|to|called|named|that|which|on)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = self._clean_target_reference(match.group(1).strip(" ."))
        return value or None

    def _extract_literal_content(self, text):
        quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
        if len(quoted) >= 2:
            return quoted[-1].strip()
        match = re.search(r"\b(?:with|saying|content|text)\s+(.+)$", text, flags=re.IGNORECASE)
        if not match:
            return None
        content = match.group(1).strip()
        if len(content) <= 300 and " code " not in f" {content.lower()} ":
            return content
        return None

    def _resolve_directory(self, params):
        directory = params.get("directory") or params.get("path")
        if not directory:
            return Path.cwd()
        candidate = self._path_from_fragment(directory)
        if candidate is not None:
            return candidate
        return Path.cwd()

    def _path_from_fragment(self, fragment):
        if fragment is None:
            return None
        raw = str(fragment).strip().strip("\"'")
        if not raw:
            return Path.cwd()
        lowered_raw = self._normalize_query(raw)
        if re.search(r"\b(?:which\s+is\s+inside|inside|under|within)\b", lowered_raw):
            nested = self._resolve_nested_directory(raw)
            if nested is not None:
                return nested
        raw = self._clean_directory_reference(raw)
        if not raw:
            return Path.cwd()
        if raw.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", raw):
            raw = raw.lstrip("/\\")

        normalized = self._normalize_query(raw)
        if normalized in {"current directory", "current folder", "this directory", "this folder", "here"}:
            return Path.cwd()
        if normalized in {"recycle bin", "trash"} and self.is_windows:
            return Path("shell:RecycleBinFolder")

        if normalized in self.standard_paths:
            return self.standard_paths[normalized]

        path = Path(raw).expanduser()
        if path.is_absolute():
            return path

        for alias, base in self.standard_paths.items():
            if normalized.startswith(f"{alias}\\") or normalized.startswith(f"{alias}/") or normalized.startswith(f"{alias} "):
                suffix = raw[len(alias) :].lstrip("\\/ ").strip()
                return (base / suffix).resolve()

        if normalized.startswith("current folder") or normalized.startswith("current directory"):
            suffix = raw.split(" ", 2)[-1] if " " in raw else ""
            if suffix and suffix.lower() not in {"folder", "directory"}:
                return (Path.cwd() / suffix).resolve()
            return Path.cwd()

        if "contents of " in normalized:
            normalized = normalized.replace("contents of ", "", 1).strip()
            raw = raw.replace("contents of ", "", 1).strip()
            if normalized in self.standard_paths:
                return self.standard_paths[normalized]
            nested = self._resolve_nested_directory(raw)
            if nested is not None:
                return nested

        path = Path(raw).expanduser()
        return (Path.cwd() / path).resolve()

    def _resolve_path(self, params, expect_existing=False, source_hint=None):
        explicit_path = params.get("path")
        name = params.get("name")
        directory = params.get("directory")

        if explicit_path:
            candidate = self._path_from_fragment(explicit_path)
            if candidate is not None and (not expect_existing or self._path_exists(candidate)):
                return candidate

        if name:
            spoken = self._normalize_spoken_filename(name)
            name_path = Path(spoken)
            parent = self._path_from_fragment(directory) if directory is not None else Path.cwd()
            if name_path.is_absolute():
                candidate = name_path
            else:
                candidate = (parent / name_path).resolve()
            if not expect_existing or self._path_exists(candidate):
                return candidate

            fuzzy = self._find_closest_path(name_path.name, start_dir=parent, source_hint=source_hint)
            if fuzzy is not None:
                return fuzzy

            if directory is None:
                for fallback_root in self._default_search_roots():
                    fuzzy = self._find_closest_path(name_path.name, start_dir=fallback_root, source_hint=source_hint)
                    if fuzzy is not None:
                        return fuzzy

        return None

    def _path_exists(self, path):
        if self.is_windows and str(path).lower() == "shell:recyclebinfolder":
            return True
        return Path(path).exists()

    def _normalize_spoken_filename(self, value):
        text = self._clean_target_reference(value)
        if not text:
            return ""
        text = re.sub(r"\bunderscore\b", "_", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:hyphen|dash)\b", "-", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*([_.-])\s*", r"\1", text)
        lowered = self._normalize_query(text)
        spoken_match = re.search(r"(.+?)\s+dot\s+([a-z0-9]+)$", lowered)
        if spoken_match:
            stem = self._clean_target_reference(spoken_match.group(1)).strip()
            extension = spoken_match.group(2).strip()
            if extension == "pi":
                extension = "py"
            return f"{stem}.{extension}"
        match = re.search(r"(.+?)\.([a-z0-9]+)$", lowered)
        if match:
            stem = self._clean_target_reference(match.group(1)).strip()
            extension = match.group(2).strip()
            if extension == "pi":
                extension = "py"
            return f"{stem}.{extension}"
        compact = text.replace(" .", ".").replace(". ", ".")
        compact = re.sub(r"\s*([_.-])\s*", r"\1", compact)
        return compact

    def _find_closest_path(self, target_name, start_dir, source_hint=None):
        start = Path(start_dir or Path.cwd())
        if not start.exists():
            return None

        target = str(target_name or "").strip()
        if not target:
            return None

        candidates = []
        for root, dirs, files in os.walk(start):
            dirs[:] = [item for item in dirs if item not in self.PROJECT_SKIP_DIRS]
            entries = list(dirs) + list(files)
            for entry in entries:
                entry_path = Path(root) / entry
                if source_hint == "file" and entry_path.is_dir():
                    continue
                if source_hint == "directory" and not entry_path.is_dir():
                    continue
                candidates.append(entry_path)
                if len(candidates) >= self.PATH_SCAN_LIMIT:
                    break
            if len(candidates) >= self.PATH_SCAN_LIMIT:
                break

        if not candidates:
            return None

        name_map = {candidate.name.lower(): candidate for candidate in candidates}
        stem_map = {candidate.stem.lower(): candidate for candidate in candidates if candidate.is_file()}
        lowered_target = target.lower()

        if lowered_target in name_map:
            return name_map[lowered_target]
        if lowered_target in stem_map:
            return stem_map[lowered_target]

        names = list(name_map.keys()) + list(stem_map.keys())
        match = difflib.get_close_matches(lowered_target, names, n=1, cutoff=0.55)
        if not match:
            partial = [candidate for candidate in candidates if lowered_target in candidate.name.lower()]
            return sorted(partial, key=lambda item: len(str(item)))[0] if partial else None
        best = match[0]
        return name_map.get(best) or stem_map.get(best)

    def _collect_search_candidates(self, start_dir, source_hint=None):
        candidates = []
        for root, dirs, files in os.walk(start_dir):
            dirs[:] = [item for item in dirs if item not in self.PROJECT_SKIP_DIRS]
            entries = list(dirs) + list(files)
            for entry in entries:
                entry_path = Path(root) / entry
                if source_hint == "file" and entry_path.is_dir():
                    continue
                if source_hint == "directory" and not entry_path.is_dir():
                    continue
                candidates.append(entry_path)
                if len(candidates) >= self.PATH_SCAN_LIMIT:
                    return candidates
        return candidates

    def _run_process(self, args, shell=False, timeout=30):
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            check=False,
        )
        output = completed.stdout.strip() or completed.stderr.strip()
        if completed.returncode != 0:
            raise RuntimeError(output or f"Command failed with exit code {completed.returncode}")
        return output

    def _powershell(self, script, timeout=45):
        if not self.is_windows:
            raise RuntimeError("This action is only implemented on Windows.")
        return self._run_process(["powershell", "-NoProfile", "-Command", script], timeout=timeout)

    @staticmethod
    def _ps_quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    def _find_executable(self, command_name):
        if str(command_name).endswith(":"):
            return command_name
        if command_name and Path(str(command_name)).is_absolute() and Path(str(command_name)).exists():
            return str(command_name)
        located = shutil.which(command_name)
        if located:
            return located
        if not self.is_windows:
            return None
        try:
            output = self._run_process(["where.exe", command_name], timeout=5)
            first = next((line.strip() for line in output.splitlines() if line.strip()), "")
            if first:
                return first
        except Exception:
            pass

        common_locations = {
            "brave.exe": [
                Path(os.getenv("ProgramFiles", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                Path(os.getenv("ProgramFiles(x86)", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                Path(os.getenv("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ],
            "code.exe": [
                Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                Path(os.getenv("ProgramFiles", "")) / "Microsoft VS Code" / "Code.exe",
                Path(os.getenv("ProgramFiles(x86)", "")) / "Microsoft VS Code" / "Code.exe",
            ],
            "vlc.exe": [
                Path(os.getenv("ProgramFiles", "")) / "VideoLAN" / "VLC" / "vlc.exe",
                Path(os.getenv("ProgramFiles(x86)", "")) / "VideoLAN" / "VLC" / "vlc.exe",
            ],
            "blender.exe": list((Path(os.getenv("ProgramFiles", "")) / "Blender Foundation").glob("Blender*\\blender.exe")),
            "matlab.exe": list((Path(os.getenv("ProgramFiles", "")) / "MATLAB").glob("R*\\bin\\matlab.exe")),
        }
        for candidate in common_locations.get(str(command_name).lower(), []):
            if candidate.exists():
                return str(candidate)
        return None

    def _is_undoable_action(self, action):
        return action in {
            "create_file",
            "create_folder",
            "delete_path",
            "modify_file",
            "copy_path",
            "move_path",
            "rename_path",
            "duplicate_path",
            "project_code",
        }

    def _push_undo_record(self, record):
        if not record:
            return
        self.undo_stack.append(record)
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _delete_path_quiet(self, path):
        target = Path(path)
        if not target.exists():
            return
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            try:
                target.unlink()
            except OSError:
                pass

    def _copy_item(self, source, destination, is_dir):
        source_path = Path(source)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if is_dir:
            if destination_path.exists():
                shutil.rmtree(destination_path)
            shutil.copytree(source_path, destination_path)
        else:
            shutil.copy2(source_path, destination_path)

    def _restore_file_snapshot(self, path, exists, content_bytes):
        target = Path(path)
        if exists:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content_bytes or b"")
            return
        self._delete_path_quiet(target)

    def _reserve_undo_backup_path(self, original):
        target = Path(original)
        safe_name = target.name.replace(" ", "_")
        stamp = int(time.time() * 1000)
        bucket = self.undo_cache_dir / f"{stamp}_{safe_name}"
        index = 1
        while bucket.exists():
            bucket = self.undo_cache_dir / f"{stamp}_{index}_{safe_name}"
            index += 1
        return bucket

    def _apply_undo_record(self, record, direction):
        mode = str(direction or "undo").lower()
        if mode not in {"undo", "redo"}:
            raise RuntimeError("Invalid undo direction.")

        kind = str(record.get("kind") or "")
        if kind == "write_file":
            if mode == "undo":
                self._restore_file_snapshot(record["path"], record.get("before_exists", False), record.get("before_bytes", b""))
            else:
                self._restore_file_snapshot(record["path"], record.get("after_exists", True), record.get("after_bytes", b""))
            return

        if kind == "create_folder":
            target = Path(record["path"])
            if mode == "undo":
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
            else:
                target.mkdir(parents=True, exist_ok=True)
            return

        if kind == "delete_move":
            original = Path(record["original"])
            backup = Path(record["backup"])
            if mode == "undo":
                if original.exists():
                    self._delete_path_quiet(original)
                if backup.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(backup), str(original))
            else:
                if backup.exists():
                    self._delete_path_quiet(backup)
                if original.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(original), str(backup))
            return

        if kind == "move_item":
            source = Path(record["source"])
            destination = Path(record["destination"])
            if mode == "undo":
                if destination.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    if source.exists():
                        self._delete_path_quiet(source)
                    shutil.move(str(destination), str(source))
            else:
                if source.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        self._delete_path_quiet(destination)
                    shutil.move(str(source), str(destination))
            return

        if kind == "copy_item":
            source = Path(record["source"])
            destination = Path(record["destination"])
            is_dir = bool(record.get("is_dir"))
            if mode == "undo":
                self._delete_path_quiet(destination)
            else:
                if not source.exists():
                    raise RuntimeError(f"Cannot redo copy; source no longer exists: {source}")
                self._copy_item(source, destination, is_dir=is_dir)
            return

        if kind == "project_batch":
            snapshots = record.get("snapshots") or []
            ordered = snapshots if mode == "redo" else list(reversed(snapshots))
            for item in ordered:
                path = item.get("path")
                if not path:
                    continue
                before_exists = bool(item.get("before_exists"))
                after_exists = bool(item.get("after_exists"))
                before_bytes = item.get("before_bytes", b"")
                after_bytes = item.get("after_bytes", b"")
                if mode == "undo":
                    self._restore_file_snapshot(path, before_exists, before_bytes)
                else:
                    self._restore_file_snapshot(path, after_exists, after_bytes)
            return

        raise RuntimeError("This action cannot be undone.")

    def _undo_last_action(self, params):
        if not self.undo_stack:
            return "There is no previous reversible command to undo."
        record = self.undo_stack.pop()
        try:
            self._apply_undo_record(record, "undo")
        except Exception:
            self.undo_stack.append(record)
            raise
        self.redo_stack.append(record)
        label = str(record.get("label") or "last action")
        return f"Undid {label}."

    def _redo_last_action(self, params):
        if not self.redo_stack:
            return "There is no command to redo."
        record = self.redo_stack.pop()
        try:
            self._apply_undo_record(record, "redo")
        except Exception:
            self.redo_stack.append(record)
            raise
        self.undo_stack.append(record)
        label = str(record.get("label") or "last action")
        return f"Redid {label}."

    def _open_path(self, params):
        website = params.get("website")
        if website:
            return self._open_website(params)

        target = self._resolve_path(params, expect_existing=True)
        if target is None:
            application = params.get("application") or params.get("name")
            if application:
                fallback_params = dict(params)
                fallback_params["application"] = application
                return self._open_application(fallback_params)
            return "No file, folder, or website matched that request."

        if params.get("application"):
            app_data = params.get("resolved_application") or self.resolve_application_request(params)
            if app_data.get("status") == "missing":
                requested = app_data.get("requested") or params.get("application")
                return f"I could not find an installed application matching {requested}."
            match = ApplicationMatch(
                requested=app_data.get("requested") or params.get("application") or "",
                display_name=app_data.get("display_name") or str(params.get("application") or ""),
                kind=app_data.get("kind") or "executable",
                command=app_data.get("command") or "",
                aliases=tuple(app_data.get("aliases") or ()),
                exact=app_data.get("status") == "exact",
            )
            self._launch_application_match(match, arguments=[str(target)])
            return f"Opened {target} in {match.display_name}."

        if self.is_windows and str(target).lower() == "shell:recyclebinfolder":
            os.startfile(str(target))
            return "Opened Recycle Bin."

        if self.is_windows:
            os.startfile(str(target))
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return f"Opened {target}."

    def _find_browser_executable(self, browser):
        alias = self.BROWSER_ALIASES.get(browser, browser)
        return self._find_executable(alias)

    def _open_website(self, params):
        url = self._resolve_website_url(params)
        browser = str(params.get("browser") or "").strip().lower()

        if browser:
            executable = self._find_browser_executable(browser)
            if executable:
                subprocess.Popen([executable, url])
                return f"Opened {url} in {browser}."

        if self.is_windows:
            os.startfile(url)
        else:
            webbrowser.open(url)
        return f"Opened {url}."

    def _open_application(self, params):
        requested = str(params.get("application") or params.get("name") or "").strip()
        if not requested:
            return "No application name was provided."
        app_data = params.get("resolved_application") or self.resolve_application_request(params)
        if app_data.get("status") == "missing":
            return f"I could not find an installed application matching {requested}."

        match = ApplicationMatch(
            requested=app_data.get("requested") or requested,
            display_name=app_data.get("display_name") or requested,
            kind=app_data.get("kind") or "executable",
            command=app_data.get("command") or "",
            aliases=tuple(app_data.get("aliases") or ()),
            exact=app_data.get("status") == "exact",
        )

        raw_text = self._normalize_query(params.get("raw_text") or "")
        if match.display_name.lower().startswith("camera") or requested.lower() in {"camera", "camera app"}:
            camera_mode = ""
            if any(token in raw_text for token in {"record", "recording", "video"}):
                camera_mode = "video"
            elif any(token in raw_text for token in {"picture", "photo", "capture", "selfie"}):
                camera_mode = "photo"
            camera_running = self._query_matching_processes(names=["windowscamera", "camera"], titles=["camera"]).get("count", 0) > 0
            if not camera_running:
                self._launch_application_match(match)
            if camera_mode:
                self._trigger_camera_shortcut(camera_mode, warmup_seconds=2.3 if not camera_running else 0.3)
                return "Captured a picture." if camera_mode == "photo" else "Started camera recording."
            return "Opened Camera."

        self._launch_application_match(match)
        return f"Launched {match.display_name}."

    def _close_application(self, params):
        requested = str(params.get("application") or params.get("name") or "").strip()
        if not requested:
            return "No application was provided to close."
        app_data = params.get("resolved_application") or self.resolve_application_request(params)
        match = None
        if app_data.get("status") != "missing":
            match = ApplicationMatch(
                requested=app_data.get("requested") or requested,
                display_name=app_data.get("display_name") or requested,
                kind=app_data.get("kind") or "executable",
                command=app_data.get("command") or "",
                aliases=tuple(app_data.get("aliases") or ()),
                exact=app_data.get("status") == "exact",
            )
        names, titles = self._process_hints_for_application(requested, match)
        result = self._stop_matching_processes(names=names, titles=titles)
        process_names = ", ".join(sorted(set(result.get("names") or [])))
        return f"Closed {requested}." if not process_names else f"Closed {requested} ({process_names})."

    def _list_directory(self, params):
        directory = self._resolve_directory(params)
        if not directory.exists():
            fuzzy = self._find_closest_path(directory.name, directory.parent, source_hint="directory")
            if fuzzy is None:
                return f"Directory not found: {directory}"
            directory = fuzzy
        if not directory.is_dir():
            return f"Not a directory: {directory}"

        items = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        if not items:
            return f"{directory} is empty."

        lines = [f"Contents of {directory}:"]
        for index, item in enumerate(items):
            if index >= self.LIST_MAX_ITEMS:
                lines.append("... (truncated)")
                break
            prefix = "[DIR]" if item.is_dir() else "[FILE]"
            lines.append(f"{prefix} {item.name}")
        return "\n".join(lines)

    def _create_file(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            return "No file name was provided."

        before_exists = target.exists() and target.is_file()
        before_bytes = target.read_bytes() if before_exists else b""
        target.parent.mkdir(parents=True, exist_ok=True)
        content = params.get("content")
        if not content and params.get("content_request"):
            content = self._generate_file_content(target, str(params["content_request"]))
        if content is None:
            target.touch(exist_ok=True)
            after_bytes = target.read_bytes() if target.exists() and target.is_file() else b""
            self._push_undo_record(
                {
                    "kind": "write_file",
                    "label": f"create file {target.name}",
                    "path": str(target),
                    "before_exists": before_exists,
                    "before_bytes": before_bytes,
                    "after_exists": target.exists() and target.is_file(),
                    "after_bytes": after_bytes,
                }
            )
            return f"Created file {target}."

        mode = "a" if str(params.get("mode", "")).lower() == "append" else "w"
        with open(target, mode, encoding="utf-8") as handle:
            handle.write(str(content))
            if content and not str(content).endswith("\n"):
                handle.write("\n")
        after_bytes = target.read_bytes() if target.exists() and target.is_file() else b""
        self._push_undo_record(
            {
                "kind": "write_file",
                "label": f"create file {target.name}",
                "path": str(target),
                "before_exists": before_exists,
                "before_bytes": before_bytes,
                "after_exists": target.exists() and target.is_file(),
                "after_bytes": after_bytes,
            }
        )
        return f"Wrote {target}."

    def _create_folder(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            return "No folder name was provided."
        existed_before = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if not existed_before:
            self._push_undo_record(
                {
                    "kind": "create_folder",
                    "label": f"create folder {target.name}",
                    "path": str(target),
                }
            )
        return f"Created folder {target}."

    def _delete_path(self, params):
        target = self._resolve_path(params, expect_existing=True)
        if target is None:
            return "No matching file or folder was found to delete."
        was_dir = target.is_dir()
        backup = self._reserve_undo_backup_path(target)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
        self._push_undo_record(
            {
                "kind": "delete_move",
                "label": f"delete {target.name}",
                "original": str(target),
                "backup": str(backup),
            }
        )
        return f"Deleted {'folder' if was_dir else 'file'} {target}."

    def _modify_file(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            return "No file was provided to modify."

        before_exists = target.exists() and target.is_file()
        before_bytes = target.read_bytes() if before_exists else b""
        before_text = self._decode_bytes(before_bytes)

        content = params.get("content")
        if not content and params.get("content_request"):
            request = str(params["content_request"])
            requested_mode = str(params.get("mode") or "").lower()
            if requested_mode == "append":
                content = self._generate_file_content(target, request)
            else:
                content = self._generate_modified_file_content(target, before_text, request)
        if not content:
            return "No new content was provided."

        target.parent.mkdir(parents=True, exist_ok=True)
        mode = str(params.get("mode") or ("overwrite" if params.get("content_request") else "append")).lower()
        write_mode = "w" if mode in {"overwrite", "replace"} else "a"
        with open(target, write_mode, encoding="utf-8") as handle:
            handle.write(str(content))
            if not str(content).endswith("\n"):
                handle.write("\n")
        after_bytes = target.read_bytes() if target.exists() and target.is_file() else b""
        after_text = self._decode_bytes(after_bytes)
        self._push_undo_record(
            {
                "kind": "write_file",
                "label": f"modify file {target.name}",
                "path": str(target),
                "before_exists": before_exists,
                "before_bytes": before_bytes,
                "after_exists": target.exists() and target.is_file(),
                "after_bytes": after_bytes,
            }
        )
        lines = [
            f"File changed: {target}",
            "Before:",
            self._with_line_numbers(before_text, max_lines=220) if before_exists else "[missing]",
            "After:",
            self._with_line_numbers(after_text, max_lines=220),
        ]
        return "\n".join(lines)

    def _decode_bytes(self, payload):
        data = payload if isinstance(payload, (bytes, bytearray)) else b""
        if not data:
            return ""
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _generate_modified_file_content(self, target_path, before_text, request):
        cleaned_request = str(request or "").strip()
        if not cleaned_request:
            return before_text
        lowered_request = cleaned_request.lower()
        normalized_request = self._normalize_query(cleaned_request)
        target_name = Path(target_path).name.lower()
        if "cv2" in normalized_request and any(token in normalized_request for token in {"without", "not use", "remove", "dont use", "do not use"}):
            if target_name.endswith(".py") and any(token in target_name for token in {"ascii", "video"}):
                return self._ascii_video_without_cv2_template()
        if self.llm:
            prompt = (
                "You are updating an existing source file for a desktop coding assistant.\n"
                "Return only the full updated file content with no markdown fences.\n"
                f"File path: {target_path}\n"
                f"Update request: {cleaned_request}\n"
                "Current file content:\n"
                f"{before_text}\n"
            )
            generated = str(self.llm.generate(prompt)).strip()
            generated = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", generated)
            generated = re.sub(r"\s*```$", "", generated)
            if generated:
                return generated
        fallback = self._generate_file_content(target_path, cleaned_request)
        if fallback:
            return fallback
        return before_text

    def _ascii_video_without_cv2_template(self):
        return (
            "import argparse\n"
            "import math\n"
            "import shutil\n"
            "import subprocess\n"
            "import time\n\n"
            "ASCII_CHARS = \" .,:;irsXA253hMHGS#9B&@\"\n\n\n"
            "def _run_ffprobe(video_path):\n"
            "    command = [\n"
            "        \"ffprobe\",\n"
            "        \"-v\",\n"
            "        \"error\",\n"
            "        \"-select_streams\",\n"
            "        \"v:0\",\n"
            "        \"-show_entries\",\n"
            "        \"stream=width,height,r_frame_rate\",\n"
            "        \"-of\",\n"
            "        \"default=noprint_wrappers=1:nokey=1\",\n"
            "        video_path,\n"
            "    ]\n"
            "    result = subprocess.run(command, capture_output=True, text=True, check=True)\n"
            "    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]\n"
            "    if len(values) < 3:\n"
            "        raise RuntimeError(\"Could not read video metadata from ffprobe.\")\n"
            "    width = int(values[0])\n"
            "    height = int(values[1])\n"
            "    rate = values[2]\n"
            "    if \"/\" in rate:\n"
            "        num, den = rate.split(\"/\", 1)\n"
            "        fps = float(num) / float(den or 1)\n"
            "    else:\n"
            "        fps = float(rate)\n"
            "    return width, height, max(1.0, fps)\n\n\n"
            "def _pixel_to_ascii(pixel):\n"
            "    index = min(len(ASCII_CHARS) - 1, int((pixel / 255) * (len(ASCII_CHARS) - 1)))\n"
            "    return ASCII_CHARS[index]\n\n\n"
            "def _frame_to_ascii(frame_bytes, width, height):\n"
            "    rows = []\n"
            "    for y in range(height):\n"
            "        start = y * width\n"
            "        row = frame_bytes[start:start + width]\n"
            "        rows.append(\"\".join(_pixel_to_ascii(pixel) for pixel in row))\n"
            "    return \"\\n\".join(rows)\n\n\n"
            "def play_ascii_video(video_path, out_width=None):\n"
            "    src_width, src_height, fps = _run_ffprobe(video_path)\n"
            "    terminal_width = shutil.get_terminal_size((100, 40)).columns - 2\n"
            "    output_width = max(40, min(out_width or terminal_width, terminal_width))\n"
            "    aspect = src_height / float(src_width or 1)\n"
            "    output_height = max(1, int(math.ceil(output_width * aspect * 0.5)))\n"
            "    frame_size = output_width * output_height\n"
            "    ffmpeg_command = [\n"
            "        \"ffmpeg\",\n"
            "        \"-loglevel\",\n"
            "        \"error\",\n"
            "        \"-i\",\n"
            "        video_path,\n"
            "        \"-vf\",\n"
            "        f\"scale={output_width}:{output_height}\",\n"
            "        \"-f\",\n"
            "        \"rawvideo\",\n"
            "        \"-pix_fmt\",\n"
            "        \"gray\",\n"
            "        \"-\",\n"
            "    ]\n"
            "    process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE)\n"
            "    if process.stdout is None:\n"
            "        raise RuntimeError(\"Failed to stream frames from ffmpeg.\")\n"
            "    frame_delay = 1.0 / max(1.0, fps)\n"
            "    try:\n"
            "        while True:\n"
            "            frame = process.stdout.read(frame_size)\n"
            "            if len(frame) < frame_size:\n"
            "                break\n"
            "            print(\"\\x1b[2J\\x1b[H\", end=\"\")\n"
            "            print(_frame_to_ascii(frame, output_width, output_height))\n"
            "            time.sleep(frame_delay)\n"
            "    finally:\n"
            "        process.kill()\n\n\n"
            "def main():\n"
            "    parser = argparse.ArgumentParser(description=\"Play a video in terminal as ASCII art frames.\")\n"
            "    parser.add_argument(\"video_path\", help=\"Path to video file\")\n"
            "    parser.add_argument(\"--width\", type=int, default=None, help=\"Output ASCII width\")\n"
            "    args = parser.parse_args()\n"
            "    play_ascii_video(args.video_path, out_width=args.width)\n\n\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n"
        )

    def _read_file(self, params):
        target = self._resolve_path(params, expect_existing=True, source_hint="file")
        if target is None:
            return "No readable file matched that request."
        if target.is_dir():
            return f"{target} is a directory, not a file."

        data = target.read_bytes()
        if b"\x00" in data:
            return f"{target} appears to be a binary file."

        text = None
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")
        if len(text) > self.READ_MAX_CHARS:
            text = text[: self.READ_MAX_CHARS] + "\n... (truncated)"
        return f"Contents of {target}:\n{text}"

    def _change_directory(self, params):
        directory = self._resolve_directory(params)
        if not directory.exists():
            fuzzy = self._find_closest_path(directory.name, directory.parent, source_hint="directory")
            if fuzzy is None:
                return f"Directory not found: {directory}"
            directory = fuzzy
        if not directory.is_dir():
            return f"Not a directory: {directory}"
        os.chdir(directory)
        self.base_dir = directory.resolve()
        return f"Changed directory to {self.base_dir}."

    def _resolve_transfer_source(self, params):
        source_dir = params.get("source_dir")
        if source_dir:
            local = dict(params)
            local["directory"] = source_dir
            return self._resolve_path(local, expect_existing=True)
        return self._resolve_path(params, expect_existing=True)

    def _resolve_transfer_destination(self, params, source):
        destination = params.get("destination")
        if not destination:
            raise RuntimeError("No destination was provided.")
        destination_path = self._path_from_fragment(destination)
        if destination_path is None:
            raise RuntimeError("Could not resolve the destination.")
        if destination_path.exists() and destination_path.is_dir():
            return (destination_path / source.name).resolve()
        if destination_path.suffix:
            return destination_path
        return (destination_path / source.name).resolve()

    def _copy_path(self, params):
        source = self._resolve_transfer_source(params)
        if source is None:
            return "No source file or folder matched that copy request."
        destination = self._resolve_transfer_destination(params, source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        self._push_undo_record(
            {
                "kind": "copy_item",
                "label": f"copy {source.name}",
                "source": str(source),
                "destination": str(destination),
                "is_dir": source.is_dir(),
            }
        )
        return f"Copied {source} to {destination}."

    def _move_path(self, params):
        source = self._resolve_transfer_source(params)
        if source is None:
            return "No source file or folder matched that move request."
        destination = self._resolve_transfer_destination(params, source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        self._push_undo_record(
            {
                "kind": "move_item",
                "label": f"move {Path(destination).name}",
                "source": str(source),
                "destination": str(destination),
            }
        )
        return f"Moved {source} to {destination}."

    def _rename_path(self, params):
        target = self._resolve_transfer_source(params)
        if target is None:
            return "No file or folder matched that rename request."
        new_name = self._normalize_spoken_filename(params.get("new_name") or "")
        if not new_name:
            return "No new name was provided."
        destination = target.with_name(new_name)
        target.rename(destination)
        self._push_undo_record(
            {
                "kind": "move_item",
                "label": f"rename {destination.name}",
                "source": str(target),
                "destination": str(destination),
            }
        )
        return f"Renamed {target.name} to {destination.name}."

    def _duplicate_path(self, params):
        source = self._resolve_transfer_source(params)
        if source is None:
            return "No file or folder matched that duplicate request."
        destination_input = str(params.get("destination") or "").strip()
        if destination_input:
            destination = self._resolve_transfer_destination(params, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            self._push_undo_record(
                {
                    "kind": "copy_item",
                    "label": f"duplicate {source.name}",
                    "source": str(source),
                    "destination": str(destination),
                    "is_dir": source.is_dir(),
                }
            )
            return f"Duplicated {source} to {destination}."
        suffix = source.suffix
        stem = source.stem
        index = 1
        while True:
            candidate_name = f"{stem}_copy{'' if index == 1 else index}{suffix}"
            destination = source.with_name(candidate_name)
            if not destination.exists():
                break
            index += 1
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        self._push_undo_record(
            {
                "kind": "copy_item",
                "label": f"duplicate {source.name}",
                "source": str(source),
                "destination": str(destination),
                "is_dir": source.is_dir(),
            }
        )
        return f"Duplicated {source} to {destination}."

    def _shutdown_system(self, params):
        if self.is_windows:
            os.system("shutdown /s /t 0")
        else:
            os.system("shutdown -h now")
        return "Shutting down the device."

    def _restart_system(self, params):
        if self.is_windows:
            os.system("shutdown /r /t 0")
        else:
            os.system("shutdown -r now")
        return "Restarting the device."

    def _sleep_system(self, params):
        if self.is_windows:
            try:
                import ctypes

                result = ctypes.windll.powrprof.SetSuspendState(False, True, False)
                if result == 0:
                    raise RuntimeError("SetSuspendState returned 0.")
            except Exception:
                # Fallback for systems where direct API invocation is blocked.
                self._run_process(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=20)
        else:
            os.system("systemctl suspend")
        return "Putting the device to sleep."

    def _set_brightness(self, params):
        percent = params.get("percent")
        direction = params.get("direction")
        if percent is None and direction:
            percent = 70 if direction == "up" else 30
        if percent is None:
            raise RuntimeError("No brightness level was provided.")
        percent = max(0, min(100, int(percent)))
        if self.is_windows:
            script = (
                "$monitors = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods; "
                f"foreach ($m in $monitors) {{ $m.WmiSetBrightness(1,{percent}) | Out-Null }}"
            )
            self._powershell(script)
            return f"Brightness set to {percent} percent."
        if shutil.which("brightnessctl"):
            self._run_process(["brightnessctl", "set", f"{percent}%"])
            return f"Brightness set to {percent} percent."
        raise RuntimeError("Brightness control is not available on this system.")

    def _windows_audio_endpoint_script(self, flow, *, percent=None, delta=None, mute_action=""):
        target_level = "" if percent is None else max(0, min(100, int(percent))) / 100.0
        delta_level = 0.0 if delta is None else float(delta)
        return f"""
$flow = {int(flow)}
$targetLevel = {'$null' if percent is None else target_level}
$delta = {delta_level}
$muteAction = {self._ps_quote(mute_action)}
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
        if not self.is_windows or AudioUtilities is None or IAudioEndpointVolume is None or CLSCTX_ALL is None:
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

    def _set_audio_endpoint(self, params, *, flow, label):
        percent = params.get("percent")
        direction = str(params.get("direction") or "").lower()
        turn_on = params.get("on")
        mute_action = ""
        delta = None

        if percent is not None:
            percent = max(0, min(100, int(percent)))
            if percent == 0:
                mute_action = "mute"
            else:
                mute_action = "unmute"
        elif direction == "down":
            delta = -(self.DEFAULT_VOLUME_STEP / 100.0)
        elif direction == "up":
            delta = self.DEFAULT_VOLUME_STEP / 100.0
        elif turn_on is False:
            mute_action = "mute"
        elif turn_on is True:
            mute_action = "unmute"
            percent = 50
        else:
            raise RuntimeError(f"No {label.lower()} level or state was provided.")

        if self.is_windows:
            state = self._python_audio_state(flow, percent=percent, delta=delta, mute_action=mute_action)
            if state is None:
                payload = self._powershell(
                    self._windows_audio_endpoint_script(flow, percent=percent, delta=delta, mute_action=mute_action),
                    timeout=25,
                )
                state = json.loads(payload)
                if not isinstance(state, dict):
                    state = {}
            final_level = int(state.get("level") or state.get("Level") or 0)
            is_muted = bool(state.get("muted") if "muted" in state else state.get("Muted", False))
            if is_muted or final_level == 0:
                return f"{label} turned off."
            return f"{label} set to {final_level} percent."

        if shutil.which("pactl"):
            target = "@DEFAULT_SINK@" if flow == 0 else "@DEFAULT_SOURCE@"
            if mute_action == "mute":
                self._run_process(["pactl", "set-source-mute" if flow else "set-sink-mute", target, "1"])
                return f"{label} turned off."
            if mute_action == "unmute":
                self._run_process(["pactl", "set-source-mute" if flow else "set-sink-mute", target, "0"])
            if percent is not None:
                self._run_process(["pactl", "set-source-volume" if flow else "set-sink-volume", target, f"{percent}%"])
                return f"{label} set to {percent} percent."
            if delta is not None:
                amount = f"{abs(int(delta * 100))}%"
                command = ["pactl", "set-source-volume" if flow else "set-sink-volume", target, f"{'+' if delta > 0 else '-'}{amount}"]
                self._run_process(command)
                direction_text = "up" if delta > 0 else "down"
                return f"{label} adjusted {direction_text}."
        raise RuntimeError(f"{label} control is not available on this system.")

    def _set_volume(self, params):
        return self._set_audio_endpoint(params, flow=0, label="System sound")

    def _set_microphone(self, params):
        return self._set_audio_endpoint(params, flow=1, label="Microphone")

    def _normalize_setting_name(self, value):
        lowered = self._normalize_query(value)
        aliases = {
            "sound": "volume",
            "volume": "volume",
            "microphone": "microphone",
            "mic": "microphone",
            "wifi": "wifi",
            "bluetooth": "bluetooth",
            "airplane mode": "airplane mode",
            "brightness": "brightness",
            "energy saver": "energy saver",
            "night light": "night light",
            "vpn": "vpn",
            "display": "display",
            "screen saver": "screen saver",
            "screensaver": "screen saver",
        }
        for key, target in aliases.items():
            if key in lowered:
                return target
        return lowered.strip()

    def _windows_bluetooth_adapters(self):
        script = r"""
$devices = @(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object {
    $_.InstanceId -match '^(USB|PCI|ACPI)\\' -and
    $_.FriendlyName -and
    $_.FriendlyName -notmatch 'Enumerator|RFCOMM|Transport|Service|Protocol TDI|Device \(RFCOMM'
} | Select-Object Status, FriendlyName, InstanceId)
$devices | ConvertTo-Json -Depth 4 -Compress
""".strip()
        payload = self._powershell(script, timeout=20)
        if not payload:
            return []
        data = json.loads(payload)
        if isinstance(data, dict):
            data = [data]
        return [item for item in data if isinstance(item, dict) and item.get("InstanceId")]

    def _windows_audio_status(self, flow, label):
        state = self._python_audio_state(flow, percent=None, delta=None, mute_action="")
        if state is None:
            script = self._windows_audio_endpoint_script(flow, percent=None, delta=None, mute_action="")
            payload = self._powershell(script, timeout=20)
            state = json.loads(payload)
            if not isinstance(state, dict):
                state = {}
        level = int(state.get("level") or state.get("Level") or 0)
        muted = bool(state.get("muted") if "muted" in state else state.get("Muted", False))
        if muted or level == 0:
            return f"{label} is off."
        return f"{label} is at {level} percent."

    def _open_setting_panel(self, params):
        setting = self._normalize_setting_name(params.get("setting") or params.get("raw_text") or "")
        if not self.is_windows:
            raise RuntimeError("Settings panels are only implemented on Windows in this build.")
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

    def _get_setting_status(self, params):
        setting = self._normalize_setting_name(params.get("setting") or params.get("raw_text") or "")
        if setting == "wifi":
            output = self._run_process(["netsh", "interface", "show", "interface"], timeout=20)
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
            output = self._run_process(["netsh", "interface", "show", "interface"], timeout=20)
            enabled_count = sum(1 for line in output.splitlines() if "Enabled" in line and ("Wi-Fi" in line or "Bluetooth" in line or "Wireless" in line))
            return "Airplane mode appears to be on." if enabled_count == 0 else "Airplane mode appears to be off."
        if setting == "brightness":
            script = "(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness | Select-Object -First 1 -ExpandProperty CurrentBrightness)"
            value = self._powershell(script)
            return f"Brightness is at {value.strip()} percent."
        if setting == "volume":
            return self._windows_audio_status(0, "System sound")
        if setting == "microphone":
            return self._windows_audio_status(1, "Microphone")
        if setting == "energy saver":
            output = self._run_process(["powercfg", "/getactivescheme"], timeout=20)
            return "Energy saver is on." if self.POWER_SAVER_GUID.lower() in output.lower() else "Energy saver is off."
        if setting == "night light":
            return "Night Light status is not directly available in this build."
        if setting == "vpn":
            script = (
                "$vpn = Get-VpnConnection -ErrorAction SilentlyContinue | Where-Object { $_.ConnectionStatus -eq 'Connected' } | "
                "Select-Object -First 1 -ExpandProperty Name; "
                "if ($vpn) { \"VPN is connected: $vpn.\" } else { 'VPN is not connected.' }"
            )
            return self._powershell(script)
        if setting == "display":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "\"Display resolution is $($bounds.Width) by $($bounds.Height).\""
            )
            return self._powershell(script)
        if setting == "screen saver":
            script = "(Get-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop' -Name ScreenSaveActive).ScreenSaveActive"
            value = self._powershell(script).strip()
            return "Screen saver is on." if value == "1" else "Screen saver is off."
        raise RuntimeError(f"Status is not available for {setting or 'that setting'}.")

    def _wireless_interface_names(self):
        if not self.is_windows:
            return []
        output = self._run_process(["netsh", "interface", "show", "interface"], timeout=20)
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

    def _set_wifi(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            errors = []
            for name in self._wireless_interface_names():
                try:
                    self._run_process(
                        ["netsh", "interface", "set", "interface", name, f"admin={'enabled' if turn_on else 'disabled'}"],
                        timeout=20,
                    )
                    return f"Wi-Fi turned {'on' if turn_on else 'off'}."
                except Exception as exc:
                    errors.append(str(exc))
            raise RuntimeError(errors[0] if errors else "No Wi-Fi interface was found.")
        if shutil.which("nmcli"):
            self._run_process(["nmcli", "radio", "wifi", "on" if turn_on else "off"])
            return f"Wi-Fi turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Wi-Fi control is not available on this system.")

    def _set_bluetooth(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            adapters = self._windows_bluetooth_adapters()
            if not adapters:
                raise RuntimeError("Bluetooth adapter not found.")
            errors = []
            for adapter in adapters:
                command = "Enable-PnpDevice" if turn_on else "Disable-PnpDevice"
                script = (
                    f"{command} -InstanceId {self._ps_quote(adapter['InstanceId'])} -Confirm:$false -ErrorAction Stop | Out-Null"
                )
                try:
                    self._powershell(script, timeout=25)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0].strip())
            if errors:
                os.startfile("ms-settings:bluetooth")
                joined = "; ".join(errors)
                raise RuntimeError(
                    f"Bluetooth {'enable' if turn_on else 'disable'} requires device permission or administrator access. "
                    f"Opened Bluetooth settings. Details: {joined}"
                )
            return f"Bluetooth turned {'on' if turn_on else 'off'}."
        if shutil.which("rfkill"):
            self._run_process(["rfkill", "unblock" if turn_on else "block", "bluetooth"])
            return f"Bluetooth turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Bluetooth control is not available on this system.")

    def _set_airplane_mode(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            updates = []
            errors = []
            try:
                updates.append(self._set_wifi({"on": not turn_on}))
            except Exception as exc:
                errors.append(str(exc).splitlines()[0].strip())
            try:
                updates.append(self._set_bluetooth({"on": not turn_on}))
            except Exception as exc:
                errors.append(str(exc).splitlines()[0].strip())
            if errors and not updates:
                raise RuntimeError("; ".join(errors))
            if errors:
                return f"Airplane mode changed with partial success. {' '.join(updates)} {'; '.join(errors)}"
            return f"Airplane mode turned {'on' if turn_on else 'off'}."
        if shutil.which("nmcli"):
            self._run_process(["nmcli", "radio", "all", "off" if turn_on else "on"])
            return f"Airplane mode turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Airplane mode control is not available on this system.")

    def _set_energy_saver(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            self._run_process(
                ["powercfg", "/setactive", self.POWER_SAVER_GUID if turn_on else self.BALANCED_POWER_GUID],
                timeout=20,
            )
            return f"Energy saver turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Energy saver control is not available on this system.")

    def _set_night_light(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            os.startfile("ms-settings:nightlight")
            return f"Opened Night Light settings to turn it {'on' if turn_on else 'off'}."
        raise RuntimeError("Night light control is not available on this system.")

    def _eject_drive(self, params):
        requested = str(params.get("name") or params.get("path") or "").strip()
        if not requested:
            raise RuntimeError("No drive was provided.")
        if self.is_windows:
            drive = requested.rstrip("\\/:")
            script = f"(New-Object -comObject Shell.Application).Namespace(17).ParseName({self._ps_quote(drive)}).InvokeVerb('Eject')"
            self._powershell(script)
            return f"Ejected {requested}."
        if shutil.which("udisksctl"):
            self._run_process(["udisksctl", "power-off", "-b", requested])
            return f"Ejected {requested}."
        raise RuntimeError("Drive ejection is not available on this system.")

    def _run_as_admin(self, params):
        application = str(params.get("application") or params.get("name") or "").strip()
        if not application:
            raise RuntimeError("No application was provided.")
        if self.is_windows:
            app_data = params.get("resolved_application") or self.resolve_application_request(params)
            executable = app_data.get("command") if app_data.get("kind") == "executable" else self._find_executable(application) or application
            script = f"Start-Process -FilePath {self._ps_quote(executable)} -Verb RunAs"
            self._powershell(script)
            return f"Launched {application} as administrator."
        raise RuntimeError("Run-as-administrator is only implemented on Windows.")

    def _play_music(self, params):
        raw_text = self._normalize_query(params.get("raw_text") or "")
        requested = str(params.get("song") or params.get("name") or "").strip()
        if not requested:
            raise RuntimeError("No song or video request was provided.")
        platform_name = str(params.get("platform") or "").strip().lower()
        if "youtube music" in raw_text:
            platform_name = "youtube_music"
        elif "spotify" in raw_text:
            platform_name = "spotify"
        elif "youtube" in raw_text or not platform_name:
            platform_name = "youtube"

        song = self._extract_media_search_query(requested, raw_text)
        browser = params.get("browser")
        if platform_name == "spotify":
            if self.is_windows:
                os.startfile(f"spotify:search:{song}")
                return f"Opened Spotify for {song}."
            url = f"https://open.spotify.com/search/{quote_plus(song)}"
        elif platform_name == "youtube_music":
            url = f"https://music.youtube.com/search?q={quote_plus(song)}"
        else:
            url = f"https://www.youtube.com/results?search_query={quote_plus(song)}"
        open_params = {"website": url}
        if browser:
            open_params["browser"] = browser
        self._open_website(open_params)
        if platform_name == "spotify":
            return f"Opened Spotify for {song}."
        if platform_name == "youtube_music":
            return f"Showing YouTube Music search results for {song}."
        return f"Showing YouTube search results for {song}."

    def _draw_file_tree(self, params):
        if params.get("current"):
            directory = Path.cwd().resolve()
            if not directory.exists():
                return f"Path not found: {directory}"
            return draw_file_tree(directory, max_depth=None, max_items=None)
        explicit_directory = str(params.get("directory") or params.get("path") or "").strip()
        if explicit_directory:
            directory = self._resolve_directory(params)
            if not directory.exists():
                fuzzy = self._find_closest_path(directory.name, directory.parent, source_hint="directory")
                if fuzzy is None and Path.cwd().anchor:
                    fuzzy = self._find_closest_path(directory.name, Path(Path.cwd().anchor), source_hint="directory")
                if fuzzy is not None:
                    directory = fuzzy
        else:
            cwd = Path.cwd().resolve()
            directory = Path(cwd.anchor) if cwd.anchor else cwd
        if not directory.exists():
            return f"Path not found: {directory}"
        return draw_file_tree(directory, max_depth=None, max_items=None)

    def _project_code(self, params):
        project_path = str(params.get("project_path") or "").strip()
        instruction = str(params.get("instruction") or params.get("_project_instruction") or "").strip()
        if not project_path:
            raise RuntimeError("No project path was provided.")

        target = self._path_from_fragment(project_path)
        if target is None:
            raise RuntimeError("Could not resolve the project path.")

        create_request = any(word in instruction.lower() for word in {"create", "build", "scaffold", "generate"})
        if (not target.exists() or target.name.lower() == "new_project") and create_request:
            changes = self._scaffold_project(target, instruction)
            self._push_project_undo_record(target, changes)
            return self._format_project_change_report(target, instruction, changes)

        if not target.exists():
            raise RuntimeError(f"Project path not found: {target}")
        if target.is_file():
            changes = self._apply_single_file_instruction(target, instruction)
            self._push_project_undo_record(target.parent, changes)
            return self._format_project_change_report(target.parent, instruction, changes)
        if not target.is_dir():
            raise RuntimeError(f"Project path is not a directory or file: {target}")

        changes = self._apply_project_instruction(target, instruction)
        self._push_project_undo_record(target, changes)
        return self._format_project_change_report(target, instruction, changes)

    def _push_project_undo_record(self, project_root, changes):
        if not changes:
            return
        snapshots = []
        for change in changes:
            absolute_path = (Path(project_root) / change.path).resolve()
            snapshots.append(
                {
                    "path": str(absolute_path),
                    "before_exists": bool(change.before_exists),
                    "after_exists": bool(change.after_exists),
                    "before_bytes": str(change.before or "").encode("utf-8"),
                    "after_bytes": str(change.after or "").encode("utf-8"),
                }
            )
        self._push_undo_record({"kind": "project_batch", "label": "project changes", "snapshots": snapshots})

    def _scaffold_project(self, project_root, instruction):
        if "fastapi" in instruction.lower():
            return self._create_fastapi_project(project_root)
        return self._create_generic_project(project_root, instruction)

    def _create_generic_project(self, project_root, instruction):
        files = {
            "README.md": f"# {project_root.name}\n\nGenerated from instruction:\n\n{instruction}\n",
            "main.py": (
                "def main():\n"
                "    print('Project scaffold generated by the assistant.')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "requirements.txt": "",
        }
        changes = []
        for relative_path, content in files.items():
            changes.append(self._write_project_file(project_root, relative_path, content, mode="create", reason="project scaffold"))
        return changes

    def _create_fastapi_project(self, project_root):
        files = {
            "requirements.txt": (
                "fastapi>=0.115.0\n"
                "uvicorn[standard]>=0.30.0\n"
                "sqlalchemy>=2.0.0\n"
                "passlib[bcrypt]>=1.7.4\n"
                "python-jose[cryptography]>=3.3.0\n"
                "pydantic>=2.0.0\n"
            ),
            "README.md": (
                f"# {project_root.name}\n\n"
                "FastAPI project scaffolded by the assistant.\n\n"
                "Features:\n"
                "- JWT authentication\n"
                "- SQLite database\n"
                "- User registration and login routes\n"
                "- Protected profile endpoint\n"
            ),
            "database.py": (
                "from sqlalchemy import create_engine\n"
                "from sqlalchemy.orm import declarative_base, sessionmaker\n\n"
                "DATABASE_URL = 'sqlite:///./app.db'\n\n"
                "engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})\n"
                "SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)\n"
                "Base = declarative_base()\n\n"
                "def get_db():\n"
                "    db = SessionLocal()\n"
                "    try:\n"
                "        yield db\n"
                "    finally:\n"
                "        db.close()\n"
            ),
            "models.py": (
                "from sqlalchemy import Column, Integer, String\n\n"
                "from database import Base\n\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n\n"
                "    id = Column(Integer, primary_key=True, index=True)\n"
                "    username = Column(String(100), unique=True, nullable=False, index=True)\n"
                "    hashed_password = Column(String(255), nullable=False)\n"
            ),
            "schemas.py": (
                "from pydantic import BaseModel\n\n\n"
                "class UserCreate(BaseModel):\n"
                "    username: str\n"
                "    password: str\n\n\n"
                "class UserLogin(BaseModel):\n"
                "    username: str\n"
                "    password: str\n\n\n"
                "class TokenResponse(BaseModel):\n"
                "    access_token: str\n"
                "    token_type: str = 'bearer'\n\n\n"
                "class UserResponse(BaseModel):\n"
                "    id: int\n"
                "    username: str\n\n"
                "    class Config:\n"
                "        from_attributes = True\n"
            ),
            "security.py": (
                "from datetime import datetime, timedelta, timezone\n\n"
                "from jose import JWTError, jwt\n"
                "from passlib.context import CryptContext\n\n"
                "SECRET_KEY = 'change-this-secret'\n"
                "ALGORITHM = 'HS256'\n"
                "ACCESS_TOKEN_EXPIRE_MINUTES = 60\n"
                "password_context = CryptContext(schemes=['bcrypt'], deprecated='auto')\n\n\n"
                "def hash_password(password: str) -> str:\n"
                "    return password_context.hash(password)\n\n\n"
                "def verify_password(password: str, hashed_password: str) -> bool:\n"
                "    return password_context.verify(password, hashed_password)\n\n\n"
                "def create_access_token(subject: str) -> str:\n"
                "    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)\n"
                "    payload = {'sub': subject, 'exp': expire}\n"
                "    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)\n\n\n"
                "def decode_access_token(token: str) -> str | None:\n"
                "    try:\n"
                "        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])\n"
                "        return payload.get('sub')\n"
                "    except JWTError:\n"
                "        return None\n"
            ),
            "routes/__init__.py": "",
            "routes/auth.py": (
                "from fastapi import APIRouter, Depends, HTTPException, status\n"
                "from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer\n"
                "from sqlalchemy.orm import Session\n\n"
                "from database import get_db\n"
                "from models import User\n"
                "from schemas import TokenResponse, UserCreate, UserLogin, UserResponse\n"
                "from security import create_access_token, decode_access_token, hash_password, verify_password\n\n"
                "router = APIRouter(prefix='/auth', tags=['auth'])\n"
                "bearer_scheme = HTTPBearer(auto_error=False)\n\n\n"
                "@router.post('/register', response_model=UserResponse, status_code=status.HTTP_201_CREATED)\n"
                "def register(payload: UserCreate, db: Session = Depends(get_db)):\n"
                "    existing = db.query(User).filter(User.username == payload.username).first()\n"
                "    if existing:\n"
                "        raise HTTPException(status_code=400, detail='Username already exists.')\n"
                "    user = User(username=payload.username, hashed_password=hash_password(payload.password))\n"
                "    db.add(user)\n"
                "    db.commit()\n"
                "    db.refresh(user)\n"
                "    return user\n\n\n"
                "@router.post('/login', response_model=TokenResponse)\n"
                "def login(payload: UserLogin, db: Session = Depends(get_db)):\n"
                "    user = db.query(User).filter(User.username == payload.username).first()\n"
                "    if user is None or not verify_password(payload.password, user.hashed_password):\n"
                "        raise HTTPException(status_code=401, detail='Invalid username or password.')\n"
                "    token = create_access_token(user.username)\n"
                "    return TokenResponse(access_token=token)\n\n\n"
                "def get_current_user(\n"
                "    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),\n"
                "    db: Session = Depends(get_db),\n"
                "):\n"
                "    if credentials is None:\n"
                "        raise HTTPException(status_code=401, detail='Missing authorization header.')\n"
                "    username = decode_access_token(credentials.credentials)\n"
                "    if not username:\n"
                "        raise HTTPException(status_code=401, detail='Invalid or expired token.')\n"
                "    user = db.query(User).filter(User.username == username).first()\n"
                "    if user is None:\n"
                "        raise HTTPException(status_code=404, detail='User not found.')\n"
                "    return user\n"
            ),
            "main.py": (
                "from fastapi import Depends, FastAPI\n\n"
                "from database import Base, engine\n"
                "from routes.auth import get_current_user, router as auth_router\n"
                "from schemas import UserResponse\n\n"
                "app = FastAPI(title='Assistant Generated FastAPI App')\n"
                "Base.metadata.create_all(bind=engine)\n"
                "app.include_router(auth_router)\n\n\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return {'status': 'ok'}\n\n\n"
                "@app.get('/me', response_model=UserResponse)\n"
                "def me(current_user = Depends(get_current_user)):\n"
                "    return current_user\n"
            ),
        }
        changes = []
        for relative_path, content in files.items():
            changes.append(self._write_project_file(project_root, relative_path, content, mode="create", reason="FastAPI scaffold"))
        return changes

    def _apply_project_instruction(self, project_root, instruction):
        context = self._load_project_context(project_root)
        if not self.llm:
            raise RuntimeError("No LLM engine is available for project-wide code changes.")

        prompt = (
            "You are updating a local project for a desktop coding assistant.\n"
            "Return only JSON with this exact shape:\n"
            '{"summary":"short summary","changes":[{"path":"relative/path.py","mode":"create|update|delete","reason":"why","content":"full file content or empty for delete"}]}\n'
            "Do not include markdown fences.\n"
            "Preserve unrelated behavior.\n"
            f"Project root: {project_root}\n"
            f"User request: {instruction}\n"
            "Project files:\n"
            f"{context}\n"
        )
        raw = self.llm.generate(prompt)
        payload = self._extract_json(raw)
        changes = []
        for item in payload.get("changes", []):
            relative_path = str(item.get("path") or "").strip().replace("\\", "/")
            if not relative_path:
                continue
            mode = str(item.get("mode") or "update").strip().lower()
            reason = str(item.get("reason") or "").strip()
            content = str(item.get("content") or "")
            target = (project_root / relative_path).resolve()
            before_exists = target.exists() and target.is_file()
            before = target.read_text(encoding="utf-8") if target.exists() and target.is_file() else ""
            if mode == "delete":
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                changes.append(
                    ProjectChange(
                        path=relative_path,
                        before=before,
                        after="",
                        mode="delete",
                        reason=reason,
                        before_exists=before_exists,
                        after_exists=False,
                    )
                )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            changes.append(
                ProjectChange(
                    path=relative_path,
                    before=before,
                    after=content,
                    mode=mode,
                    reason=reason,
                    before_exists=before_exists,
                    after_exists=True,
                )
            )
        return changes

    def _apply_single_file_instruction(self, target_file, instruction):
        file_path = Path(target_file).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError(f"File not found: {file_path}")
        if not self.llm:
            raise RuntimeError("No LLM engine is available for single-file updates.")

        before = file_path.read_text(encoding="utf-8")
        effective_instruction = instruction or (
            "Correct the code so it is valid, coherent, and aligned with its likely purpose."
        )
        prompt = (
            "You are updating one source file for a desktop coding assistant.\n"
            "Return only the full updated file content. Do not return markdown fences.\n"
            f"File path: {file_path}\n"
            f"User request: {effective_instruction}\n"
            "Current file content:\n"
            f"{before}\n"
        )
        generated = str(self.llm.generate(prompt)).strip()
        generated = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", generated)
        generated = re.sub(r"\s*```$", "", generated)
        if not generated:
            raise RuntimeError("The model did not return updated file content.")

        file_path.write_text(generated, encoding="utf-8")
        return [
            ProjectChange(
                path=file_path.name,
                before=before,
                after=generated,
                mode="update",
                reason="single-file update",
                before_exists=True,
                after_exists=True,
            )
        ]

    def _load_project_context(self, project_root):
        sections = []
        count = 0
        for path in self._iter_project_files(project_root):
            if count >= self.PROJECT_MAX_FILES:
                sections.append("... additional files omitted ...")
                break
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            relative = path.relative_to(project_root).as_posix()
            sections.append(f"FILE: {relative}\n{content}\n")
            count += 1
        return "\n".join(sections)

    def _iter_project_files(self, root) -> Iterable[Path]:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            if any(part in self.PROJECT_SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in self.TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > self.PROJECT_MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path

    def _write_project_file(self, project_root, relative_path, content, mode="create", reason=""):
        target = (project_root / relative_path).resolve()
        before_exists = target.exists() and target.is_file()
        before = target.read_text(encoding="utf-8") if before_exists else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ProjectChange(
            path=relative_path.replace("\\", "/"),
            before=before,
            after=content,
            mode=mode,
            reason=reason,
            before_exists=before_exists,
            after_exists=True,
        )

    def _format_project_change_report(self, project_root, instruction, changes):
        lines = [
            f"Project: {project_root}",
            f"Instruction: {instruction or 'No instruction provided.'}",
        ]
        if not changes:
            lines.append("No files were changed.")
            return "\n".join(lines)

        for change in changes:
            absolute_path = (Path(project_root) / change.path).resolve()
            lines.append("")
            lines.append(f"File changed: {change.path}")
            lines.append(f"Absolute path: {absolute_path}")
            if change.reason:
                lines.append(f"Reason: {change.reason}")
            lines.append("Before:")
            lines.append(
                self._with_line_numbers(change.before, max_lines=180)
                if change.before
                else ("[deleted]" if change.mode == "delete" else "[missing]")
            )
            lines.append("After:")
            if change.mode == "delete":
                lines.append("[deleted]")
            else:
                lines.append(self._with_line_numbers(change.after, max_lines=180) if change.after else "[empty file]")
        return "\n".join(lines)

    def _with_line_numbers(self, text, max_lines=180):
        lines = str(text or "").splitlines()
        if not lines:
            return "[empty]"
        output = []
        for index, line in enumerate(lines, start=1):
            if index > max_lines:
                output.append("... (truncated)")
                break
            output.append(f"{index:4d}: {line}")
        return "\n".join(output)

    def _extract_json(self, text):
        raw = str(text or "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("The LLM did not return valid JSON.")
        return json.loads(raw[start : end + 1])

    def _generate_file_content(self, target_path, request):
        request = str(request or "").strip()
        suffix = target_path.suffix.lower()
        lowered = request.lower()

        if suffix == ".py" and "palindrome" in lowered:
            return (
                "def is_palindrome(value: str) -> bool:\n"
                "    cleaned = ''.join(ch.lower() for ch in value if ch.isalnum())\n"
                "    return cleaned == cleaned[::-1]\n\n"
                "def main() -> None:\n"
                "    user_input = input('Enter a string: ')\n"
                "    if is_palindrome(user_input):\n"
                "        print('The string is a palindrome.')\n"
                "    else:\n"
                "        print('The string is not a palindrome.')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
        if suffix == ".py" and "ascii" in lowered and "video" in lowered:
            return (
                "import argparse\n"
                "import os\n"
                "import shutil\n"
                "import time\n\n"
                "import cv2\n\n"
                "ASCII_CHARS = ' .,:;irsXA253hMHGS#9B&@'\n\n\n"
                "def frame_to_ascii(frame, width):\n"
                "    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)\n"
                "    height, current_width = gray.shape\n"
                "    if current_width <= 0:\n"
                "        return ''\n"
                "    ratio = height / float(current_width)\n"
                "    target_height = max(1, int(width * ratio * 0.5))\n"
                "    resized = cv2.resize(gray, (width, target_height), interpolation=cv2.INTER_AREA)\n"
                "    rows = []\n"
                "    for row in resized:\n"
                "        chars = [ASCII_CHARS[min(len(ASCII_CHARS) - 1, int(pixel / 255 * (len(ASCII_CHARS) - 1)))] for pixel in row]\n"
                "        rows.append(''.join(chars))\n"
                "    return '\\n'.join(rows)\n\n\n"
                "def play_ascii_video(video_path, width=100):\n"
                "    if not os.path.exists(video_path):\n"
                "        raise FileNotFoundError(f'Video not found: {video_path}')\n"
                "    capture = cv2.VideoCapture(video_path)\n"
                "    if not capture.isOpened():\n"
                "        raise RuntimeError('Unable to open video file.')\n"
                "    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0\n"
                "    frame_delay = 1.0 / max(1.0, fps)\n"
                "    try:\n"
                "        while True:\n"
                "            ok, frame = capture.read()\n"
                "            if not ok:\n"
                "                break\n"
                "            ascii_frame = frame_to_ascii(frame, width)\n"
                "            os.system('cls' if os.name == 'nt' else 'clear')\n"
                "            print(ascii_frame)\n"
                "            time.sleep(frame_delay)\n"
                "    finally:\n"
                "        capture.release()\n\n\n"
                "def main():\n"
                "    parser = argparse.ArgumentParser(description='Play a video as ASCII art in the terminal.')\n"
                "    parser.add_argument('video_path', help='Path to the video file')\n"
                "    parser.add_argument('--width', type=int, default=max(60, shutil.get_terminal_size((100, 40)).columns - 4))\n"
                "    args = parser.parse_args()\n"
                "    play_ascii_video(args.video_path, width=max(40, args.width))\n\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
        if suffix == ".py" and "add" in lowered and "two number" in lowered:
            return (
                "def main():\n"
                "    first = float(input('Enter the first number: '))\n"
                "    second = float(input('Enter the second number: '))\n"
                "    total = first + second\n"
                "    print(f'Sum: {total}')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
        if suffix == ".cpp" and "reverse" in lowered and "number" in lowered:
            return (
                "#include <iostream>\n"
                "using namespace std;\n\n"
                "int main() {\n"
                "    int number;\n"
                "    int reversed = 0;\n"
                "    cout << \"Enter a number: \";\n"
                "    cin >> number;\n"
                "    while (number != 0) {\n"
                "        reversed = reversed * 10 + (number % 10);\n"
                "        number /= 10;\n"
                "    }\n"
                "    cout << \"Reversed number: \" << reversed << endl;\n"
                "    return 0;\n"
                "}\n"
            )
        if self.llm:
            prompt = (
                "Generate only the file contents requested below. "
                "Do not include markdown fences or explanations. "
                f"File name: {target_path.name}. Request: {request}"
            )
            generated = str(self.llm.generate(prompt)).strip()
            generated = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", generated)
            generated = re.sub(r"\s*```$", "", generated)
            if generated:
                return generated
        return request + ("\n" if request and not request.endswith("\n") else "")

    def _resolve_website_url(self, params):
        website = str(params.get("website") or "").strip()
        lowered = self._normalize_query(website)

        if re.match(r"^https?://", lowered):
            return lowered
        if re.match(r"^(?:www\.)?[a-z0-9-]+\.[a-z0-9.-]+(?:/.*)?$", lowered):
            return f"https://{lowered}"
        if "github" in lowered:
            user = self._extract_site_path(lowered, "github")
            return f"https://github.com/{user}" if user else "https://github.com"
        if "youtube" in lowered:
            if "channel" in lowered or "profile" in lowered:
                handle = self._extract_handle(lowered, "youtube")
                if handle:
                    return f"https://www.youtube.com/@{handle.lstrip('@')}"
            path = self._extract_site_path(lowered, "youtube")
            return f"https://www.youtube.com/{path}" if path else "https://www.youtube.com"
        if "spotify" in lowered:
            path = self._extract_site_path(lowered, "spotify")
            return f"https://open.spotify.com/{path}" if path else "https://open.spotify.com"
        return f"https://{quote_plus(website)}"

    def _extract_site_path(self, text, site_name):
        lowered = self._normalize_query(text)
        lowered = lowered.replace(f"{site_name}/", f"{site_name} /")
        if f"{site_name} /" in lowered:
            after = lowered.split(f"{site_name} /", 1)[1]
            return after.strip().replace(" ", "")
        match = re.search(rf"{site_name}\s+([a-z0-9_./@-]+)$", lowered)
        if match:
            return match.group(1).strip().lstrip("/")
        if site_name == "github":
            match = re.search(r"\bgithub(?:\s+slash)?\s+([a-z0-9_.-]+)", lowered)
            if match:
                return match.group(1).strip()
        if site_name == "youtube":
            match = re.search(r"\bopen\s+([a-z0-9_.-]+)\s+youtube", lowered)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_handle(self, text, site_name):
        if site_name == "youtube":
            match = re.search(r"\b([a-z0-9_.-]+)\s+youtube\s+(?:channel|profile)\b", text)
            if match:
                return match.group(1).strip()
        path = self._extract_site_path(text, site_name)
        if path:
            return path.split("/", 1)[0]
        return ""

    def _update_session_context(self, action, params, result):
        if action in {"open_application", "close_application", "run_as_admin"}:
            application = params.get("application") or params.get("name")
            if application:
                self.session_context["last_application"] = str(application)
        if action in {
            "open_path",
            "read_file",
            "delete_path",
            "modify_file",
            "create_file",
            "create_folder",
            "copy_path",
            "move_path",
            "rename_path",
            "duplicate_path",
            "change_directory",
        }:
            target = self._resolve_path(params, expect_existing=False)
            if target is not None:
                self.session_context["last_path"] = str(target)
                self.session_context["last_name"] = target.name
