import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

from .utils import draw_file_tree


@dataclass
class ProjectChange:
    path: str
    before: str
    after: str
    mode: str
    reason: str = ""


class SystemActions:
    READ_MAX_CHARS = 12000
    LIST_MAX_ITEMS = 200
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
        "vscode": ["code.exe", "code"],
        "visual studio code": ["code.exe", "code"],
        "code": ["code.exe", "code"],
        "spotify": ["spotify.exe", "spotify"],
        "notepad": ["notepad.exe", "notepad"],
        "terminal": ["wt.exe", "powershell.exe", "cmd.exe"],
        "powershell": ["powershell.exe", "pwsh.exe"],
        "cmd": ["cmd.exe"],
        "camera": ["microsoft.windows.camera:"],
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

    def __init__(self, base_dir=None, llm=None):
        self.base_dir = Path(base_dir or os.getcwd()).resolve()
        self.is_windows = platform.system().lower().startswith("win")
        self.llm = llm
        self.home = Path.home()
        self.standard_paths = {
            alias: (self.home / suffix).resolve() if suffix else self.home.resolve()
            for alias, suffix in self.SPECIAL_FOLDERS.items()
        }
        self.session_context = {}

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
            "set_wifi": self._set_wifi,
            "set_bluetooth": self._set_bluetooth,
            "set_airplane_mode": self._set_airplane_mode,
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

    def build_voice_summary(self, action, params, result):
        text = str(result or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if "error" in lowered or "failed" in lowered or "not found" in lowered:
            return text
        if action in {"read_file", "list_directory", "draw_file_tree", "project_code"}:
            return text[:700]
        return text

    def clear_context(self):
        self.session_context.clear()

    def find_path_candidates(self, name, directory=None, source_hint=None, max_results=10):
        target_name = self._normalize_spoken_filename(name)
        target_lower = target_name.lower()
        target_stem = Path(target_name).stem.lower()
        roots = [self._path_from_fragment(directory)] if directory else [Path.cwd(), *self.standard_paths.values()]
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

        if not data.get("directory") and raw_text:
            directory = self._extract_directory_from_text(raw_text)
            if directory:
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
            ("recyclebin", "recycle bin"),
            ("wi fi", "wifi"),
            ("blue tooth", "bluetooth"),
            ("air plane", "airplane"),
            ("flight mode", "airplane mode"),
            ("c plus plus", "cpp"),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _refers_to_previous_target(self, normalized):
        tokens = set(re.findall(r"[a-z0-9_./\\-]+", normalized))
        return bool(tokens & {"it", "that", "there", "them", "this"})

    def _extract_application_from_text(self, text):
        lowered = self._normalize_query(text)
        for alias in sorted(self.APP_ALIASES, key=len, reverse=True):
            if alias in lowered:
                return alias
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
        match = re.search(
            r"\b(?:in|inside|under|at|from)\s+(.+?)(?=\s+\b(?:named|called|with|and|that|which|into)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        directory = match.group(1).strip(" .")
        if directory.lower() in {"text mode", "voice mode"}:
            return None
        return directory

    def _extract_target_name(self, text):
        quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
        if quoted:
            return quoted[0].strip()

        match = re.search(
            r"\b(?:create|make|generate|write|open|launch|run|start|show|delete|remove|erase|copy|move|rename|duplicate|read|list|display|play)\s+(.+?)(?=\s+\b(?:in|inside|under|at|with|from|to|called|named|that|which|on)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1).strip(" .")
        value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.IGNORECASE)
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
            return None
        if raw.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", raw):
            raw = raw.lstrip("/\\")

        normalized = self._normalize_query(raw)
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
            parent = self._path_from_fragment(directory) if directory else Path.cwd()
            if name_path.is_absolute():
                candidate = name_path
            else:
                candidate = (parent / name_path).resolve()
            if not expect_existing or self._path_exists(candidate):
                return candidate

            fuzzy = self._find_closest_path(name_path.name, start_dir=parent, source_hint=source_hint)
            if fuzzy is not None:
                return fuzzy

            if not directory:
                for fallback_root in [Path.cwd(), *self.standard_paths.values()]:
                    fuzzy = self._find_closest_path(name_path.name, start_dir=fallback_root, source_hint=source_hint)
                    if fuzzy is not None:
                        return fuzzy

        return None

    def _path_exists(self, path):
        if self.is_windows and str(path).lower() == "shell:recyclebinfolder":
            return True
        return Path(path).exists()

    def _normalize_spoken_filename(self, value):
        text = str(value or "").strip().strip("\"'")
        lowered = self._normalize_query(text)
        spoken_match = re.search(r"(.+?)\s+dot\s+([a-z0-9]+)$", lowered)
        if spoken_match:
            stem = spoken_match.group(1).strip().replace(" ", "_")
            extension = spoken_match.group(2).strip()
            if extension == "pi":
                extension = "py"
            return f"{stem}.{extension}"
        match = re.search(r"(.+?)\.([a-z0-9]+)$", lowered)
        if match:
            stem = match.group(1).strip().replace(" ", "_")
            extension = match.group(2).strip()
            if extension == "pi":
                extension = "py"
            return f"{stem}.{extension}"
        if "." not in text and " " in text:
            return "_".join(text.split())
        return text.replace(" .", ".").replace(". ", ".")

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
                if len(candidates) >= self.LIST_MAX_ITEMS * 4:
                    break
            if len(candidates) >= self.LIST_MAX_ITEMS * 4:
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
                if len(candidates) >= self.LIST_MAX_ITEMS * 4:
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
        located = shutil.which(command_name)
        if located:
            return located
        if not self.is_windows:
            return None

        search_roots = [
            Path(os.getenv("ProgramFiles", "")),
            Path(os.getenv("ProgramFiles(x86)", "")),
            Path(os.getenv("LOCALAPPDATA", "")),
        ]
        matches = []
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob(command_name):
                matches.append(path)
                if len(matches) >= 8:
                    break
            if matches:
                break
        return str(matches[0]) if matches else None

    def _open_path(self, params):
        website = params.get("website")
        if website:
            return self._open_website(params)

        target = self._resolve_path(params, expect_existing=True)
        if target is None:
            application = params.get("application") or params.get("name")
            if application:
                return self._open_application({"application": application})
            return "No file, folder, or website matched that request."

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

        alias_key = requested.lower()
        candidates = self.APP_ALIASES.get(alias_key, [requested])
        for candidate in candidates:
            executable = self._find_executable(candidate)
            if executable is None:
                continue
            if executable.endswith(":"):
                os.startfile(executable)
                return f"Launched {requested}."
            subprocess.Popen([executable])
            return f"Launched {requested}."

        if self.is_windows:
            script = f"Start-Process -FilePath {self._ps_quote(requested)}"
            self._powershell(script)
            return f"Launched {requested}."

        subprocess.Popen([requested])
        return f"Launched {requested}."

    def _close_application(self, params):
        requested = str(params.get("application") or params.get("name") or "").strip()
        if not requested:
            return "No application was provided to close."

        alias_key = requested.lower()
        executable_names = self.APP_ALIASES.get(alias_key, [requested])
        failures = []
        for executable in executable_names:
            try:
                if self.is_windows:
                    image = Path(executable).name
                    self._run_process(["taskkill", "/IM", image, "/F"], timeout=15)
                else:
                    self._run_process(["pkill", "-f", executable], timeout=15)
                return f"Closed {requested}."
            except Exception as exc:
                failures.append(str(exc))
        details = failures[0] if failures else "No running process matched."
        return f"Could not close {requested}. {details}"

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

        target.parent.mkdir(parents=True, exist_ok=True)
        content = params.get("content")
        if not content and params.get("content_request"):
            content = self._generate_file_content(target, str(params["content_request"]))
        if content is None:
            target.touch(exist_ok=True)
            return f"Created file {target}."

        mode = "a" if str(params.get("mode", "")).lower() == "append" else "w"
        with open(target, mode, encoding="utf-8") as handle:
            handle.write(str(content))
            if content and not str(content).endswith("\n"):
                handle.write("\n")
        return f"Wrote {target}."

    def _create_folder(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            return "No folder name was provided."
        target.mkdir(parents=True, exist_ok=True)
        return f"Created folder {target}."

    def _delete_path(self, params):
        target = self._resolve_path(params, expect_existing=True)
        if target is None:
            return "No matching file or folder was found to delete."
        if target.is_dir():
            shutil.rmtree(target)
            return f"Deleted folder {target}."
        target.unlink()
        return f"Deleted file {target}."

    def _modify_file(self, params):
        target = self._resolve_path(params, expect_existing=False)
        if target is None:
            return "No file was provided to modify."

        content = params.get("content")
        if not content and params.get("content_request"):
            content = self._generate_file_content(target, str(params["content_request"]))
        if not content:
            return "No new content was provided."

        target.parent.mkdir(parents=True, exist_ok=True)
        mode = str(params.get("mode", "append")).lower()
        write_mode = "w" if mode in {"overwrite", "replace"} else "a"
        with open(target, write_mode, encoding="utf-8") as handle:
            handle.write(str(content))
            if not str(content).endswith("\n"):
                handle.write("\n")
        return f"Updated {target}."

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
        return f"Copied {source} to {destination}."

    def _move_path(self, params):
        source = self._resolve_transfer_source(params)
        if source is None:
            return "No source file or folder matched that move request."
        destination = self._resolve_transfer_destination(params, source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
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
        return f"Renamed {target.name} to {destination.name}."

    def _duplicate_path(self, params):
        source = self._resolve_transfer_source(params)
        if source is None:
            return "No file or folder matched that duplicate request."
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
            self._powershell(
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"
            )
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
            state = "On" if turn_on else "Off"
            script = (
                "[void][Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]; "
                "$accessTask = [Windows.Devices.Radios.Radio]::RequestAccessAsync(); "
                "$accessTask.AsTask().Wait(); "
                "$access = $accessTask.GetResults(); "
                "if ($access -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) { throw 'Radio access denied.' } "
                "$radiosTask = [Windows.Devices.Radios.Radio]::GetRadiosAsync(); "
                "$radiosTask.AsTask().Wait(); "
                "$radios = $radiosTask.GetResults(); "
                "$bt = $radios | Where-Object { $_.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth }; "
                "if (-not $bt) { throw 'Bluetooth radio not found.' } "
                f"foreach ($radio in $bt) {{ $op = $radio.SetStateAsync([Windows.Devices.Radios.RadioState]::{state}); $op.AsTask().Wait(); }}"
            )
            self._powershell(script)
            return f"Bluetooth turned {'on' if turn_on else 'off'}."
        if shutil.which("rfkill"):
            self._run_process(["rfkill", "unblock" if turn_on else "block", "bluetooth"])
            return f"Bluetooth turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Bluetooth control is not available on this system.")

    def _set_airplane_mode(self, params):
        turn_on = bool(params.get("on", True))
        if self.is_windows:
            state = "Off" if turn_on else "On"
            script = (
                "[void][Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]; "
                "$accessTask = [Windows.Devices.Radios.Radio]::RequestAccessAsync(); "
                "$accessTask.AsTask().Wait(); "
                "$access = $accessTask.GetResults(); "
                "if ($access -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) { throw 'Radio access denied.' } "
                "$radiosTask = [Windows.Devices.Radios.Radio]::GetRadiosAsync(); "
                "$radiosTask.AsTask().Wait(); "
                "$radios = $radiosTask.GetResults(); "
                f"foreach ($radio in $radios) {{ if ($radio.Kind -ne [Windows.Devices.Radios.RadioKind]::Unknown) {{ $op = $radio.SetStateAsync([Windows.Devices.Radios.RadioState]::{state}); $op.AsTask().Wait(); }} }}"
            )
            self._powershell(script)
            return f"Airplane mode turned {'on' if turn_on else 'off'}."
        if shutil.which("nmcli"):
            self._run_process(["nmcli", "radio", "all", "off" if turn_on else "on"])
            return f"Airplane mode turned {'on' if turn_on else 'off'}."
        raise RuntimeError("Airplane mode control is not available on this system.")

    def _set_night_light(self, params):
        raise RuntimeError("Night light control is not available through this build yet.")

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
            executable = self._find_executable(application) or application
            script = f"Start-Process -FilePath {self._ps_quote(executable)} -Verb RunAs"
            self._powershell(script)
            return f"Launched {application} as administrator."
        raise RuntimeError("Run-as-administrator is only implemented on Windows.")

    def _play_music(self, params):
        song = str(params.get("song") or params.get("name") or "").strip()
        if not song:
            raise RuntimeError("No song title was provided.")
        platform_name = str(params.get("platform") or "").strip().lower()
        browser = params.get("browser")
        if platform_name == "spotify":
            url = f"https://open.spotify.com/search/{quote_plus(song)}"
        else:
            url = f"https://www.youtube.com/results?search_query={quote_plus(song)}"
        open_params = {"website": url}
        if browser:
            open_params["browser"] = browser
        self._open_website(open_params)
        if platform_name == "spotify":
            return f"Opened Spotify results for {song}."
        return f"Opened YouTube results for {song}."

    def _draw_file_tree(self, params):
        directory = self._resolve_directory(params)
        if not directory.exists():
            return f"Path not found: {directory}"
        return draw_file_tree(directory)

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
            return self._format_project_change_report(target, instruction, changes)

        if not target.exists():
            raise RuntimeError(f"Project path not found: {target}")
        if not target.is_dir():
            raise RuntimeError(f"Project path is not a directory: {target}")

        changes = self._apply_project_instruction(target, instruction)
        return self._format_project_change_report(target, instruction, changes)

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
            before = target.read_text(encoding="utf-8") if target.exists() and target.is_file() else ""
            if mode == "delete":
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                changes.append(ProjectChange(path=relative_path, before=before, after="", mode="delete", reason=reason))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            changes.append(ProjectChange(path=relative_path, before=before, after=content, mode=mode, reason=reason))
        return changes

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
        before = target.read_text(encoding="utf-8") if target.exists() and target.is_file() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ProjectChange(
            path=relative_path.replace("\\", "/"),
            before=before,
            after=content,
            mode=mode,
            reason=reason,
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
            lines.append("")
            lines.append(f"File changed: {change.path}")
            if change.reason:
                lines.append(f"Reason: {change.reason}")
            lines.append("Before:")
            lines.append(change.before[:1200] if change.before else ("[deleted]" if change.mode == "delete" else "[missing]"))
            lines.append("After:")
            if change.mode == "delete":
                lines.append("[deleted]")
            else:
                lines.append(change.after[:1200] if change.after else "[empty file]")
        return "\n".join(lines)

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
