import re
from collections import defaultdict
from difflib import get_close_matches

from .config import VOICE_PRESETS


class IntentEngine:
    VALID_ACTIONS = {
        "shutdown_system",
        "restart_system",
        "sleep_system",
        "stop_assistant",
        "undo_command",
        "redo_command",
        "open_application",
        "open_path",
        "close_application",
        "close_all_apps",
        "list_processes",
        "kill_process",
        "run_terminal_command",
        "list_directory",
        "create_file",
        "create_folder",
        "delete_path",
        "modify_file",
        "read_file",
        "change_directory",
        "copy_path",
        "move_path",
        "rename_path",
        "duplicate_path",
        "set_brightness",
        "set_volume",
        "set_microphone",
        "get_setting_status",
        "open_setting_panel",
        "set_wifi",
        "set_bluetooth",
        "set_airplane_mode",
        "set_energy_saver",
        "set_night_light",
        "eject_drive",
        "run_as_admin",
        "play_music",
        "draw_file_tree",
        "reset_password",
        "restart_setup",
        "change_assistant_name",
        "clear_history",
        "list_history",
        "open_conversation",
        "delete_conversation",
        "new_conversation",
        "read_terminal",
        "set_voice_auth",
        "set_humor",
        "change_language",
        "change_voice",
        "set_autostart",
        "set_wake_response",
        "set_wake_sensitivity",
        "set_wave_display",
        "set_bubble_display",
        "set_interface_style",
        "enter_text_mode",
        "project_code",
        "get_news",
    }

    ACTION_WORDS = {
        "open": {"open", "launch", "run", "start", "show"},
        "create": {"create", "make", "generate", "build", "write", "new"},
        "delete": {"delete", "remove", "erase", "trash"},
        "read": {"read", "show", "display", "view"},
        "modify": {"edit", "modify", "update", "append", "overwrite", "replace", "change"},
        "copy": {"copy", "clone", "paste"},
        "move": {"move", "transfer", "shift", "relocate"},
        "rename": {"rename"},
        "duplicate": {"duplicate"},
        "switch": {"set", "setup", "configure", "switch", "turn", "enable", "disable", "activate", "deactivate", "change"},
        "close": {"close", "quit", "exit", "terminate", "kill", "stop"},
        "play": {"play", "stream", "listen"},
    }
    FILE_HINTS = {
        "python": ".py",
        "py": ".py",
        "script": ".py",
        "text": ".txt",
        "txt": ".txt",
        "markdown": ".md",
        "json": ".json",
        "yaml": ".yaml",
        "csv": ".csv",
        "cpp": ".cpp",
        "c++": ".cpp",
        "java": ".java",
        "javascript": ".js",
        "typescript": ".ts",
        "html": ".html",
        "css": ".css",
    }
    WEBSITE_WORDS = {"youtube", "github", "spotify", "reddit", "gmail", "chatgpt", "google", "twitter", "x.com"}
    APP_WORDS = {
        "brave",
        "browser",
        "blender",
        "vscode",
        "code",
        "camera",
        "matlab",
        "vlc",
        "video",
        "player",
        "chrome",
        "edge",
        "firefox",
        "spotify",
        "terminal",
        "powershell",
        "cmd",
        "notepad",
        "calculator",
        "paint",
        "explorer",
        "finder",
        "file",
        "visual",
        "studio",
    }
    FOLDER_WORDS = {
        "folder",
        "directory",
        "downloads",
        "documents",
        "onedrive",
        "pictures",
        "videos",
        "desktop",
        "music",
        "recycle",
        "bin",
    }
    SETTING_WORDS = {
        "wifi",
        "bluetooth",
        "airplane",
        "brightness",
        "night",
        "light",
        "sound",
        "volume",
        "microphone",
        "mic",
        "energy",
        "saver",
        "vpn",
        "display",
        "screen",
        "screensaver",
    }
    SHELL_COMMAND_HINTS = {
        "ls",
        "cd",
        "pwd",
        "mkdir",
        "rm",
        "cp",
        "mv",
        "cat",
        "grep",
        "find",
        "ps",
        "top",
        "kill",
        "killall",
        "chmod",
        "chown",
        "tar",
        "gzip",
        "gunzip",
        "ping",
        "whois",
        "dig",
        "wget",
        "curl",
        "ssh",
        "scp",
        "rsync",
        "df",
        "du",
        "free",
        "uname",
        "uptime",
        "whoami",
        "history",
        "alias",
        "systemctl",
        "journalctl",
        "ip",
        "ifconfig",
        "netstat",
        "ss",
        "apt",
        "apt-get",
        "yum",
        "dnf",
        "snap",
        "flatpak",
        "sudo",
        "man",
        "touch",
        "head",
        "tail",
        "locate",
    }
    CONTROL_WORDS = {
        "password",
        "voice",
        "auth",
        "authentication",
        "verification",
        "humor",
        "language",
        "voice",
        "preset",
        "autostart",
        "startup",
        "login",
        "text",
        "mode",
        "wake",
        "summon",
        "call",
        "response",
        "reply",
        "sensitivity",
        "sensitvity",
        "sensitive",
        "wave",
        "waves",
        "ascii",
        "bubble",
        "interface",
        "transform",
        "process",
        "processes",
        "background",
    }

    def __init__(self, llm_engine):
        self.llm = llm_engine
        self.vocabulary = set()
        for group in self.ACTION_WORDS.values():
            self.vocabulary.update(group)
        self.vocabulary.update(self.FILE_HINTS)
        self.vocabulary.update(self.WEBSITE_WORDS)
        self.vocabulary.update(self.APP_WORDS)
        self.vocabulary.update(self.FOLDER_WORDS)
        self.vocabulary.update(self.SETTING_WORDS)
        self.vocabulary.update(self.CONTROL_WORDS)
        self.vocabulary.update(VOICE_PRESETS)
        self.vocabulary.update(
            {"project", "channel", "profile", "slash", "percent", "setup", "history", "terminal", "name", "undo", "redo", "revert", "chat", "conversation"}
        )

    def detect(self, text, context=None):
        original = str(text or "").strip()
        if not original:
            return {"intent": "conversation", "parameters": {}}

        if original.startswith("@"):
            return self._detect_project_command(original)
        embedded_project = self._detect_embedded_project_command(original)
        if embedded_project:
            return embedded_project

        normalized = self._normalize_query_text(original)
        tokens = self._correct_tokens(self._tokenize(normalized))
        action = self._detect_action(original, normalized, tokens)
        current_context = context or {}
        if action is None and current_context.get("last_project_root"):
            if self._contains_phrase(
                normalized,
                {
                    "in this project",
                    "in the project",
                    "in this codebase",
                    "in the codebase",
                    "for this project",
                },
            ) or (
                self._contains_any(tokens, {"project", "codebase"})
                and self._contains_any(
                    tokens,
                    self.ACTION_WORDS["modify"] | self.ACTION_WORDS["create"] | {"fix", "refactor", "rewrite"},
                )
            ):
                action = "project_code"
        previous_application = str(current_context.get("last_application") or "").strip().lower()
        if action is None and previous_application.startswith("camera") and self._contains_any(
            tokens,
            {"picture", "pictures", "photo", "capture", "selfie", "record", "recording", "video"},
        ):
            action = "open_application"

        if action:
            return {
                "intent": "system_command",
                "action": action,
                "parameters": self._extract_params(original, normalized, tokens, action, current_context),
            }

        llm_result = self._llm_intent_fallback(original, current_context)
        if llm_result:
            return llm_result

        return {"intent": "conversation", "parameters": {"raw_text": original}}

    def _detect_project_command(self, text):
        body = text[1:].strip()
        if not body:
            return {"intent": "conversation", "parameters": {"raw_text": text}}
        if body.startswith(("/", "\\")):
            body = body[1:].strip()
        if body.startswith(("'", '"')):
            quote = body[0]
            end = body.find(quote, 1)
            if end > 1:
                project_path = body[1:end].strip().strip("\"'")
                instruction = body[end + 1 :].strip()
            else:
                project_path = body.strip(quote).strip()
                instruction = ""
        else:
            parts = body.split(maxsplit=1)
            project_path = parts[0].strip().strip("\"'")
            instruction = parts[1].strip() if len(parts) > 1 else ""
        return {
            "intent": "system_command",
            "action": "project_code",
            "parameters": {
                "project_path": project_path,
                "instruction": instruction,
                "raw_text": text,
            },
        }

    def _detect_embedded_project_command(self, text):
        match = re.search(r"@/([^\s]+)", str(text or ""))
        if not match:
            return None
        project_path = match.group(1).strip().strip("\"'")
        if not project_path:
            return None
        after = str(text or "")[match.end() :].strip()
        instruction = re.sub(r"^(?:to|for)\s+", "", after, flags=re.IGNORECASE).strip()
        if not instruction:
            before = str(text or "")[: match.start()].strip()
            candidate = re.sub(
                r"^(?:update|modify|change|fix|edit|refactor|rewrite|improve|create|generate|build)\s+(?:the\s+)?(?:code|project)?\s*(?:in|inside|within)?\s*",
                "",
                before,
                flags=re.IGNORECASE,
            ).strip()
            instruction = candidate
        return {
            "intent": "system_command",
            "action": "project_code",
            "parameters": {
                "project_path": project_path,
                "instruction": instruction,
                "raw_text": text,
            },
        }

    def _normalize_query_text(self, text):
        normalized = str(text or "").lower()
        normalized = re.sub(
            r"\bdot\s+((?:[a-z0-9]\s+){1,7}[a-z0-9])\b",
            lambda match: "dot " + match.group(1).replace(" ", ""),
            normalized,
        )
        replacements = (
            ("wi-fi", "wifi"),
            ("wi fi", "wifi"),
            ("why fi", "wifi"),
            ("blue tooth", "bluetooth"),
            ("blu tooth", "bluetooth"),
            ("air plane", "airplane"),
            ("flight mode", "airplane mode"),
            ("enery saver", "energy saver"),
            ("battery saver", "energy saver"),
            ("night ligh", "night light"),
            ("sound level", "volume level"),
            ("system sound", "volume"),
            ("one drive", "onedrive"),
            ("vs code", "visual studio code"),
            ("versus code", "visual studio code"),
            ("verses code", "visual studio code"),
            ("verse code", "visual studio code"),
            ("vscode", "visual studio code"),
            ("c v two", "cv2"),
            ("c v 2", "cv2"),
            ("cv two", "cv2"),
            ("dot pi", "dot py"),
            (" dot py ", ".py "),
            ("c plus plus", "cpp"),
            ("control space", "ctrl space"),
            ("text interface", "text mode"),
            ("humour", "humor"),
            ("sensitvity", "sensitivity"),
            ("sensivity", "sensitivity"),
            ("diable", "disable"),
            ("forward slash", "/"),
            (" slash ", "/"),
            (" dot ", "."),
            (" underscore ", "_"),
            (" hyphen ", "-"),
            (" dash ", "-"),
        )
        for source, target in replacements:
            normalized = normalized.replace(source, target)
        normalized = re.sub(r"\bunderscore\b", "_", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\b(?:hyphen|dash)\b", "-", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*([_.-])\s*", r"\1", normalized)
        normalized = re.sub(r"(\d+)\s*%", r"\1 percent", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _tokenize(self, normalized):
        raw_tokens = re.findall(r"[a-z0-9@./\\:+_-]+", normalized)
        cleaned = []
        for token in raw_tokens:
            token = token.strip(".,!?;:()[]{}\"'")
            if token:
                cleaned.append(token)
        return cleaned

    def _correct_tokens(self, tokens):
        corrected = []
        for token in tokens:
            if len(token) <= 2 or token in self.vocabulary:
                corrected.append(token)
                continue
            if any(char in token for char in "@/\\.") or token.isdigit():
                corrected.append(token)
                continue
            match = get_close_matches(token, self.vocabulary, n=1, cutoff=0.82)
            corrected.append(match[0] if match else token)
        return corrected

    def _contains_any(self, tokens, values):
        token_set = set(tokens)
        return any(value in token_set for value in values)

    def _contains_phrase(self, normalized, phrases):
        return any(phrase in normalized for phrase in phrases)

    def _is_file_context(self, normalized, tokens):
        if any(re.search(r"\.[a-z0-9]{1,6}\b", token) for token in tokens):
            return True
        if self._contains_any(tokens, {"file", "document", "script", "program"}):
            return True
        if "code" in set(tokens):
            app_code_phrases = {"visual studio code", "vs code", "versus code", "vscode", "code editor"}
            if not any(phrase in normalized for phrase in app_code_phrases):
                return True
        if any(hint in normalized for hint in ("text file", "python file", "cpp file")):
            return True
        return False

    def _is_folder_context(self, tokens):
        return self._contains_any(tokens, self.FOLDER_WORDS)

    def _is_website_context(self, normalized, tokens):
        if self._contains_any(tokens, self.WEBSITE_WORDS | {"channel", "profile", "website", "site"}):
            return True
        if any(token.startswith("http") for token in tokens):
            return True
        if any("/" in token and len(token) > 3 for token in tokens):
            return True
        if any(re.match(r"^(?:www\.)?[a-z0-9-]+\.[a-z0-9.-]+(?:/.*)?$", token) for token in tokens):
            return True
        if "youtube/" in normalized or "github/" in normalized or "spotify/" in normalized:
            return True
        if self._contains_any(tokens, {"search", "find", "lookup", "browse"}) and not self._contains_any(tokens, self.APP_WORDS):
            return True
        compact = [token for token in tokens if token not in {"open", "visit", "go", "to", "search", "for", "show", "launch", "start"}]
        if len(compact) == 1 and re.match(r"^[a-z0-9-]{3,30}$", compact[0]) and compact[0] not in self.APP_WORDS:
            return True
        return False

    def _detect_action(self, text, normalized, tokens):
        if re.search(r"\b(?:run|execute)\s+(?:the\s+)?(?:terminal|shell)?\s*command\b", normalized):
            return "run_terminal_command"
        scores = defaultdict(int)
        token_set = set(tokens)
        linux_cli_terms = {
            "uname",
            "whoami",
            "uptime",
            "cal",
            "date",
            "finger",
            "man",
            "whereis",
            "which",
            "ifconfig",
            "netstat",
            "ss",
            "traceroute",
            "mtr",
            "whois",
            "dig",
            "wget",
            "scp",
            "rsync",
            "locate",
            "grep",
            "sed",
            "awk",
            "cut",
            "paste",
            "tar",
            "gzip",
            "gunzip",
            "zip",
            "unzip",
            "chmod",
            "chown",
            "ln",
            "nohup",
            "jobs",
            "bg",
            "fg",
            "journalctl",
            "systemctl",
            "vmstat",
            "lscpu",
            "lsblk",
            "free",
            "df",
            "du",
        }
        linux_nl_phrases = {
            "current date and time",
            "show this month calendar",
            "who is online",
            "kernel information",
            "cpu information",
            "memory information",
            "disk usage",
            "directory space usage",
            "manual for",
            "show routing table",
            "network interfaces",
            "listening ports",
            "reverse lookup",
            "show load averages",
            "virtual memory statistics",
            "download file",
            "continue download",
            "search recursively",
            "process tree",
        }
        app_phrase_present = self._contains_phrase(
            normalized,
            {"vlc media player", "visual studio code", "file explorer", "camera app"},
        )
        explicit_app_request = app_phrase_present or self._contains_any(
            tokens,
            {
                "camera",
                "blender",
                "matlab",
                "explorer",
                "terminal",
                "powershell",
                "cmd",
                "notepad",
                "calculator",
                "paint",
                "vlc",
                "vscode",
                "code",
                "spotify",
                "firefox",
                "chrome",
                "edge",
            },
        )

        if self._contains_any(tokens, {"picture", "pictures", "photo", "capture", "selfie"}) and self._contains_any(tokens, {"click", "take", "capture"}):
            scores["open_application"] += 15
        if self._contains_any(tokens, {"record", "recording", "video"}) and self._contains_any(tokens, {"start", "begin", "record"}):
            scores["open_application"] += 14
        if self._contains_phrase(normalized, linux_nl_phrases):
            scores["run_terminal_command"] += 13
        if self._contains_any(tokens, linux_cli_terms):
            scores["run_terminal_command"] += 9

        if normalized in {
            "stop",
            "stop yourself",
            "kill yourself",
            "kill",
            "end",
            "end yourself",
            "shutdown yourself",
            "shut yourself down",
            "go offline",
            "turn yourself off",
            "turn off yourself",
            "turn off",
        } or self._contains_phrase(
            normalized,
            {"stop assistant", "exit assistant", "close assistant", "shutdown assistant", "shut down assistant"},
        ):
            scores["stop_assistant"] += 30
        if (
            self._contains_any(tokens, {"kill", "end", "terminate", "shutdown"})
            and len(token_set) <= 3
            and not self._contains_any(tokens, self.APP_WORDS | {"app", "application", "browser"})
            and not self._contains_any(tokens, {"process", "processes", "pid"})
        ):
            scores["stop_assistant"] += 20
        if self._contains_any(tokens, {"undo", "revert"}) and (
            self._contains_any(tokens, {"previous", "last", "command", "change", "changes"}) or len(token_set) <= 3
        ):
            scores["undo_command"] += 18
        if "redo" in token_set and (
            self._contains_any(tokens, {"previous", "last", "command", "change", "changes"}) or len(token_set) <= 3
        ):
            scores["redo_command"] += 18

        if "password" in token_set and self._contains_any(tokens, {"reset", "change", "update", "setup"}):
            scores["reset_password"] += 14
        if self._contains_phrase(normalized, {"set you up again", "set you up", "start setup", "start the setup", "lets start setup", "lets set you up"}) or (
            self._contains_any(tokens, {"setup", "configure"}) and self._contains_any(tokens, {"start", "restart", "again", "reconfigure"})
        ):
            scores["restart_setup"] += 16
        if "name" in token_set and self._contains_any(tokens, {"assistant", "your", "you"}) and self._contains_any(tokens, {"change", "set", "rename", "call"}):
            scores["change_assistant_name"] += 15
        if self._contains_any(tokens, {"history", "conversation"}) and self._contains_any(tokens, {"clear", "erase", "delete", "remove"}):
            scores["clear_history"] += 15
        if ("history" in token_set and self._contains_any(tokens, {"show", "list", "open", "view"})) or normalized in {"history", "open history", "show history", "list history"}:
            scores["list_history"] += 15
        if self._contains_phrase(normalized, {"open last conversation", "open last chat", "last conversation", "last chat"}):
            scores["open_conversation"] += 16
        if self._contains_any(tokens, {"chat", "conversation"}) and self._contains_any(tokens, {"open", "switch", "load", "continue"}):
            scores["open_conversation"] += 14
        if re.search(r"\b(?:chat|conversation)\s+[a-z]?\d{1,6}\b", normalized) and not self._contains_any(tokens, {"delete", "remove", "erase", "clear", "eliminate"}):
            scores["open_conversation"] += 14
        if self._contains_any(tokens, {"chat", "conversation"}) and self._contains_any(tokens, {"delete", "remove", "erase", "clear", "eliminate"}):
            scores["delete_conversation"] += 15
        if re.search(r"\b(?:delete|remove|erase|clear|eliminate)\s+[c]?\d{1,6}\b", normalized):
            scores["delete_conversation"] += 16
        if self._contains_phrase(normalized, {"new conversation", "new chat", "start new conversation", "start new chat"}):
            scores["new_conversation"] += 15
        if "terminal" in token_set and self._contains_any(tokens, {"read", "aloud", "say", "speak"}):
            scores["read_terminal"] += 15
        if "voice" in token_set and self._contains_any(tokens, {"auth", "authentication", "verification", "verify"}):
            scores["set_voice_auth"] += 15
        if "humor" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["set_humor"] += 12
        if self._contains_any(tokens, {"language", "conversation"}) and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["change_language"] += 12
        if self._contains_any(tokens, {"news", "headline", "headlines", "trending", "trend", "trends"}) or self._contains_phrase(
            normalized,
            {"what is new", "whats new", "what's new", "latest news", "top news", "top headlines", "today's headlines"},
        ):
            scores["get_news"] += 18
        if "voice" in token_set and "auth" not in token_set and self._contains_any(
            tokens,
            {"preset", "model", "setup", "configure"} | self.ACTION_WORDS["switch"],
        ):
            scores["change_voice"] += 10
            for preset in VOICE_PRESETS:
                if preset in token_set:
                    scores["change_voice"] += 4
        if self._contains_phrase(normalized, {"voice model setup", "voice model settings", "voice model configuration", "voice model cofiguration", "start voice model setup"}):
            scores["change_voice"] += 14
        if normalized in {"text mode", "open text mode", "switch to text mode", "enable text mode"}:
            scores["enter_text_mode"] += 18
        if "text" in token_set and "mode" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"open"}):
            scores["enter_text_mode"] += 14
        if self._contains_any(tokens, {"autostart", "startup", "login"}) and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["set_autostart"] += 12
        if self._contains_any(tokens, {"wake", "summon", "call"}) and self._contains_any(
            tokens,
            {"response", "reply", "ack", "acknowledgement", "acknowledgment"},
        ) and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_wake_response"] += 14
        if self._contains_any(tokens, {"wave", "waves", "ascii"}) and self._contains_any(
            tokens,
            self.ACTION_WORDS["switch"] | {"on", "off"},
        ) and not self._contains_any(tokens, {"status", "state", "check", "current", "what"}):
            scores["set_wave_display"] += 15
        if "bubble" in token_set and self._contains_any(
            tokens,
            self.ACTION_WORDS["switch"] | {"on", "off", "interface"},
        ) and not self._contains_any(tokens, {"status", "state", "check", "current", "what"}):
            scores["set_bubble_display"] += 16
        interface_style_terms = {"waves", "wave", "bubble"}
        if (
            self._contains_any(tokens, {"interface", "style", "theme", "shape", "transform"})
            and any(term in normalized for term in interface_style_terms)
            and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"transform", "to"})
        ):
            scores["set_interface_style"] += 18
        if self._contains_phrase(normalized, {"text interface", "textual interface", "plain interface", "text-only interface"}):
            scores["set_wave_display"] += 20
        if self._contains_any(tokens, {"wake", "summon", "call"}) and self._contains_any(
            tokens,
            {"sensitivity", "sensitvity", "sensitive"},
        ) and (
            self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"increase", "decrease", "reduce"})
            or "percent" in token_set
            or bool(re.search(r"\d{1,3}\s*(?:percent|%)", normalized))
            or "to" in token_set
        ):
            scores["set_wake_sensitivity"] += 16

        if self._contains_any(tokens, {"shutdown", "power"}) or self._contains_phrase(normalized, {"turn off my computer", "turn off device"}):
            scores["shutdown_system"] += 14
        if self._contains_any(tokens, {"restart", "reboot"}):
            scores["restart_system"] += 14
        if self._contains_any(tokens, {"sleep", "suspend"}):
            scores["sleep_system"] += 14

        numeric_or_word_target = self._extract_percentage_value(normalized) is not None
        brightness_control_words = {"increase", "decrease", "reduce", "raise", "lower", "dim", "brighter", "on", "off"}
        if "brightness" in token_set and (
            self._contains_any(tokens, brightness_control_words)
            or numeric_or_word_target
        ):
            scores["set_brightness"] += 13
        setting_tokens = self.SETTING_WORDS | {
            "vpn",
            "display",
            "screen",
            "screensaver",
            "wave",
            "waves",
            "ascii",
            "bubble",
            "wake",
            "summon",
            "call",
            "response",
            "model",
            "preset",
            "sensitivity",
            "sensitvity",
            "voice",
        }
        status_words = {"status", "state", "check"}
        question_words = {"what", "whats", "current", "currently", "now", "right", "percentage", "level", "value"}
        has_numeric_target = bool(re.search(r"\d{1,3}\s*(?:percent|%)", normalized))
        asks_setting_status = (
            self._contains_any(tokens, setting_tokens)
            and (
                self._contains_any(tokens, status_words)
                or (
                    self._contains_any(tokens, question_words)
                    and not has_numeric_target
                )
            )
        )
        if asks_setting_status:
            scores["get_setting_status"] += 14
        if self._contains_any(tokens, {"settings", "setting", "panel"}) and self._contains_any(
            tokens,
            self.SETTING_WORDS | {"vpn", "display", "screen", "screensaver"},
        ) and self._contains_any(tokens, self.ACTION_WORDS["open"] | {"show"}):
            scores["open_setting_panel"] += 13
        volume_control_words = {
            "up",
            "down",
            "increase",
            "decrease",
            "reduce",
            "raise",
            "lower",
            "louder",
            "quieter",
            "mute",
            "unmute",
            "on",
            "off",
            "disable",
            "enable",
            "activate",
            "deactivate",
            "turn",
        }
        if self._contains_any(tokens, {"sound", "volume", "mute", "unmute"}) and (
            self._contains_any(tokens, volume_control_words) or numeric_or_word_target
        ):
            scores["set_volume"] += 15
        microphone_control_words = volume_control_words
        if self._contains_any(tokens, {"microphone", "mic"}) and (
            self._contains_any(tokens, microphone_control_words) or numeric_or_word_target
        ):
            scores["set_microphone"] += 15
        if "wifi" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_wifi"] += 13
        if "bluetooth" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_bluetooth"] += 13
        if "airplane" in token_set and "mode" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_airplane_mode"] += 13
        if "energy" in token_set and "saver" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_energy_saver"] += 13
        if "night" in token_set and "light" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_night_light"] += 11

        if self._contains_any(tokens, self.ACTION_WORDS["create"]):
            if self._is_file_context(normalized, tokens):
                scores["create_file"] += 20
                if self._contains_any(tokens, {"write", "code", "program", "script"}) or "in which" in normalized:
                    scores["create_file"] += 8
            if self._is_folder_context(tokens):
                scores["create_folder"] += 13
        if self._contains_any(tokens, self.ACTION_WORDS["delete"]) and (self._is_file_context(normalized, tokens) or self._is_folder_context(tokens) or self._refers_to_previous_target(tokens)):
            scores["delete_path"] += 14
        if self._contains_any(tokens, self.ACTION_WORDS["read"]) and (self._is_file_context(normalized, tokens) or self._refers_to_previous_target(tokens)):
            scores["read_file"] += 12
        if self._contains_any(tokens, {"list", "browse", "contents"}) and (
            self._is_folder_context(tokens) or "files" in token_set or "inside" in token_set
        ):
            scores["list_directory"] += 12
        if self._contains_phrase(normalized, {"change directory", "switch directory", "go to directory", "go to folder"}):
            scores["change_directory"] += 12
        if self._contains_any(tokens, self.ACTION_WORDS["copy"]):
            scores["copy_path"] += 12
        if self._contains_any(tokens, self.ACTION_WORDS["move"]):
            scores["move_path"] += 12
        if self._contains_any(tokens, self.ACTION_WORDS["rename"]):
            scores["rename_path"] += 12
        if self._contains_any(tokens, self.ACTION_WORDS["duplicate"]):
            scores["duplicate_path"] += 12
            if "to" in token_set:
                scores["copy_path"] += 14
        if self._contains_any(tokens, self.ACTION_WORDS["modify"]) and (self._is_file_context(normalized, tokens) or self._refers_to_previous_target(tokens)):
            scores["modify_file"] += 13
        if self._contains_any(tokens, self.ACTION_WORDS["modify"] | {"fix", "refactor", "rewrite"}) and "code" in token_set and self._contains_any(
            tokens,
            {"project", "codebase", "folder", "directory"},
        ):
            scores["project_code"] += 15

        media_verbs = {"play", "open", "run", "start"}
        media_nouns = {"music", "song", "video", "track", "playlist", "album"}
        media_platforms = {"spotify", "youtube", "youtube_music"}
        has_media_noun = self._contains_any(tokens, media_nouns)
        has_media_platform = self._contains_any(tokens, media_platforms)
        if (
            self._contains_any(tokens, media_verbs)
            and has_media_noun
            and (has_media_platform or "channel" in token_set)
            and not self._is_file_context(normalized, tokens)
        ):
            scores["play_music"] += 16
        if self._contains_any(tokens, self.ACTION_WORDS["play"]) and (self._contains_any(tokens, {"music", "song", "spotify", "youtube"}) or len(tokens) > 1):
            scores["play_music"] += 13
        if self._contains_any(tokens, {"song", "music"}) and len(tokens) >= 2:
            scores["play_music"] += 12
        if self._contains_any(tokens, {"tree", "structure"}) and self._contains_any(tokens, {"draw", "show", "system", "directory", "folder", "file"}):
            scores["draw_file_tree"] += 12

        if self._contains_any(tokens, self.ACTION_WORDS["close"]) and (self._contains_any(tokens, self.APP_WORDS | {"app", "application", "browser"}) or self._refers_to_previous_target(tokens)):
            scores["close_application"] += 12
        if self._contains_any(tokens, self.ACTION_WORDS["close"] | {"kill", "terminate"}) and self._contains_any(
            tokens,
            {"all", "apps", "applications", "windows", "tabs"},
        ):
            scores["close_all_apps"] += 18
        if self._contains_any(tokens, {"process", "processes", "background"}) and self._contains_any(
            tokens,
            {"list", "show", "what", "running", "current", "status"},
        ):
            scores["list_processes"] += 16
        if self._contains_any(tokens, {"process", "processes", "background"}) and len(token_set) <= 3:
            scores["list_processes"] += 14
        if self._contains_any(tokens, self.ACTION_WORDS["close"] | {"kill", "terminate"}) and (
            "process" in token_set or bool(re.search(r"\bpid\b", normalized))
        ):
            scores["kill_process"] += 24
        if tokens:
            first = tokens[0]
            if first in self.SHELL_COMMAND_HINTS and (
                len(tokens) > 1 or any(flag in normalized for flag in {" -", " --", "|", ">", "<"})
            ):
                scores["run_terminal_command"] += 13

        if self._contains_any(tokens, self.ACTION_WORDS["open"]):
            file_open_request = self._is_file_context(normalized, tokens) or self._is_folder_context(tokens)
            if file_open_request and (explicit_app_request or self._contains_any(tokens, {"using", "with"})):
                scores["open_path"] += 18
                scores["open_application"] += 4
            elif self._is_website_context(normalized, tokens):
                scores["open_path"] += 15
            elif explicit_app_request:
                scores["open_application"] += 16
            elif file_open_request or self._refers_to_previous_target(tokens):
                scores["open_path"] += 13
            else:
                scores["open_application"] += 12
            if explicit_app_request or self._contains_any(tokens, self.APP_WORDS | {"app", "application", "browser"}):
                scores["open_application"] += 4

        if "play" in token_set and (self._is_file_context(normalized, tokens) or self._is_folder_context(tokens) or "directory" in token_set or "folder" in token_set):
            scores["open_path"] += 16
            if explicit_app_request:
                scores["open_application"] += 4

        if not scores and (self._contains_any(tokens, self.APP_WORDS) or app_phrase_present):
            meaningful = [token for token in tokens if token not in {"open", "run", "launch", "start", "the"}]
            if 1 <= len(meaningful) <= 5:
                scores["open_application"] += 11

        if not scores:
            return None
        action, score = max(scores.items(), key=lambda item: item[1])
        return action if score >= 9 else None

    def _extract_params(self, text, normalized, tokens, action, context):
        params = {"raw_text": text}
        lowered = normalized.lower()

        if action in {"set_brightness", "set_humor", "set_voice_auth", "set_volume", "set_microphone", "set_wake_sensitivity"}:
            value = self._extract_percentage_value(lowered)
            if value is not None:
                if action == "set_brightness":
                    params["percent"] = value
                elif action in {"set_volume", "set_microphone"}:
                    params["percent"] = value
                elif action == "set_humor":
                    params["level"] = value
                elif action == "set_wake_sensitivity":
                    params["percent"] = value
                else:
                    params["threshold"] = value
        if action == "set_brightness":
            if self._contains_any(tokens, {"increase", "raise", "brighter"}):
                params["direction"] = "up"
            elif self._contains_any(tokens, {"decrease", "reduce", "lower", "dim"}):
                params["direction"] = "down"
        if action in {"set_volume", "set_microphone"}:
            if self._contains_any(tokens, {"increase", "raise", "up", "louder"}):
                params["direction"] = "up"
            elif self._contains_any(tokens, {"decrease", "reduce", "lower", "down", "quieter"}):
                params["direction"] = "down"
            if any(word in lowered for word in ("off", "disable", "deactivate", "mute")):
                params["on"] = False
            elif any(word in lowered for word in ("on", "enable", "activate", "unmute")):
                params["on"] = True

        if action in {"set_wifi", "set_bluetooth", "set_airplane_mode", "set_energy_saver", "set_night_light", "set_autostart", "set_wake_response", "set_wave_display", "set_bubble_display", "set_interface_style"}:
            params["on"] = not any(word in lowered for word in ("off", "disable", "deactivate"))
        if action == "set_wave_display" and self._contains_phrase(
            lowered,
            {"text interface", "textual interface", "plain interface", "text-only interface"},
        ):
            params["on"] = False
        if action == "set_interface_style":
            style = self._extract_interface_style(text)
            if style:
                params["style"] = style
        if action == "set_voice_auth":
            if any(word in lowered for word in ("off", "disable", "deactivate")):
                params["threshold"] = 0
            elif any(word in lowered for word in ("enable", "activate", "on")) and "threshold" not in params:
                params["threshold"] = 50
        if action == "set_wake_sensitivity":
            if "percent" not in params:
                if self._contains_any(tokens, {"increase", "raise"}):
                    params["delta"] = +10
                elif self._contains_any(tokens, {"decrease", "reduce", "lower"}):
                    params["delta"] = -10
        if action == "change_language":
            match = re.search(r"\b(?:to|in)\s+([a-z]+)\b", lowered)
            if match:
                params["language"] = match.group(1)
        if action == "get_news":
            topic = self._extract_news_topic(text)
            if topic:
                params["topic"] = topic
        if action in {"get_setting_status", "open_setting_panel"}:
            params["setting"] = self._extract_setting_name(lowered)
        if action == "kill_process":
            pid_match = re.search(r"\b(?:pid\s*)?(\d{2,9})\b", lowered)
            if pid_match:
                params["process_id"] = int(pid_match.group(1))
            else:
                process_match = re.search(
                    r"\b(?:kill|close|terminate|stop|end)\s+(?:the\s+)?(?:process\s+)?([a-z0-9_. -]+)$",
                    lowered,
                )
                if process_match:
                    params["process"] = process_match.group(1).strip(" .")
        if action == "run_terminal_command":
            command_match = re.search(
                r"\b(?:run|execute)\s+(?:the\s+)?(?:terminal|shell)?\s*command\s+(.+)$",
                text,
                flags=re.IGNORECASE,
            )
            if command_match:
                params["command"] = command_match.group(1).strip()
            else:
                params["command"] = text.strip()
        if action == "change_voice":
            for preset in VOICE_PRESETS:
                if preset in tokens:
                    params["preset"] = preset
                    break
            if "preset" not in params:
                match = re.search(r"\b(?:to|as)\s+([a-z0-9 _-]{2,60})$", lowered, flags=re.IGNORECASE)
                if match:
                    candidate = match.group(1).strip(" .")
                    candidate = re.sub(r"\b(?:voice|model|preset|profile)\b", "", candidate, flags=re.IGNORECASE).strip(" .")
                    if candidate and candidate not in {"change", "setup", "configure"}:
                        params["preset"] = candidate
        if action == "change_assistant_name":
            name = self._extract_requested_name(text)
            if name:
                params["assistant_name"] = name
        if action in {"open_conversation", "delete_conversation"}:
            conversation_id = self._extract_conversation_id(text)
            if conversation_id:
                params["conversation_id"] = conversation_id
            if action == "open_conversation" and self._contains_phrase(lowered, {"last conversation", "last chat"}):
                params["target"] = "last"

        if action == "play_music":
            song, platform = self._extract_song_request(lowered)
            if song:
                params["song"] = song
            if platform:
                params["platform"] = platform
            if self._contains_phrase(lowered, {"incognito", "private mode", "private window"}):
                params["incognito"] = True
            if self._contains_phrase(lowered, {"new tab", "separate tab"}):
                params["new_tab"] = True
            if self._contains_phrase(lowered, {"new window", "separate window"}):
                params["new_window"] = True
            directory = self._extract_directory(text)
            if directory is not None:
                directory_lower = str(directory or "").strip().lower()
                if directory == "" or directory_lower not in {"youtube", "youtube music", "spotify"}:
                    params["directory"] = directory
            application = self._extract_application_target(text)
            if application:
                params["application"] = application

        if action in {"open_application", "close_application", "run_as_admin"}:
            entity = self._extract_entity_after_verbs(text, ("run", "open", "launch", "start", "close", "quit", "exit", "terminate", "kill"))
            previous_camera = str(context.get("last_application") or "").strip().lower().startswith("camera")
            camera_follow_up = self._contains_any(tokens, {"picture", "pictures", "photo", "capture", "selfie", "record", "recording", "video"})
            if previous_camera and camera_follow_up:
                params["application"] = context.get("last_application")
            elif entity and entity.lower() not in {"recording", "picture", "pictures", "photo", "video"}:
                params["application"] = entity
            elif self._refers_to_previous_target(tokens):
                previous = context.get("last_application")
                if previous:
                    params["application"] = previous
            elif camera_follow_up:
                params["application"] = "camera"
            else:
                params["application"] = text.strip()

        if action in {"open_path", "read_file", "delete_path", "modify_file", "change_directory"}:
            name = self._extract_target_name(text)
            if name:
                params["name"] = name
            directory = self._extract_directory(text)
            if directory is not None:
                params["directory"] = directory
            if self._refers_to_previous_target(tokens) and context.get("last_path"):
                params["path"] = context["last_path"]
                params.setdefault("name", context.get("last_name") or "")

        if action == "list_directory":
            if self._contains_phrase(lowered, {"current directory", "current folder", "this directory", "this folder", "here"}):
                directory = ""
            else:
                directory = self._extract_list_directory_target(text) or self._extract_directory(text)
            if directory is not None:
                params["directory"] = directory
            elif context.get("last_path"):
                params["directory"] = context["last_path"]

        if action in {"copy_path", "move_path", "rename_path", "duplicate_path"}:
            params.update(self._extract_transfer_params(text, context))

        if action == "create_folder":
            params["name"] = self._extract_target_name(text) or "New Folder"
            directory = self._extract_directory(text)
            if directory:
                params["directory"] = directory

        if action == "create_file":
            filename = self._extract_filename(text, lowered)
            if filename:
                params["name"] = filename
            directory = self._extract_directory(text)
            if directory:
                params["directory"] = directory
            literal = self._extract_literal_content(text)
            if literal:
                params["content"] = literal
            else:
                request = self._extract_content_request(text)
                if request:
                    params["content_request"] = request
            if "name" not in params:
                params["name"] = self._default_filename_for_request(text, lowered)

        if action == "modify_file":
            literal = self._extract_literal_content(text)
            if literal:
                params["content"] = literal
            else:
                request = self._extract_content_request(text)
                if request:
                    params["content_request"] = request
                else:
                    to_match = re.search(r"\bto\s+(.+)$", text, flags=re.IGNORECASE)
                    if to_match:
                        fallback_request = to_match.group(1).strip(" .")
                        if fallback_request:
                            params["content_request"] = fallback_request
            if any(word in lowered for word in ("overwrite", "replace")):
                params["mode"] = "overwrite"
            elif "append" in lowered:
                params["mode"] = "append"

        if action in {"open_path", "open_application"}:
            website = self._extract_website_target(text)
            if website:
                params["website"] = website
            browser = self._extract_browser(text)
            if browser:
                params["browser"] = browser
            if self._contains_phrase(lowered, {"incognito", "private mode", "private window"}):
                params["incognito"] = True
            if self._contains_phrase(lowered, {"new tab", "separate tab"}):
                params["new_tab"] = True
            if self._contains_phrase(lowered, {"new window", "separate window"}):
                params["new_window"] = True
            if action == "open_path":
                application = self._extract_application_target(text)
                if application:
                    params["application"] = application

        if action == "draw_file_tree":
            if self._contains_phrase(lowered, {"current folder", "current directory", "folder i am in", "folder i am inside", "the folder i am in", "the folder i am inside"}):
                params["current"] = True
                params["directory"] = "."
            else:
                directory = self._extract_tree_directory_target(text)
                if directory is None:
                    directory = self._extract_directory(text)
                if directory is not None:
                    params["directory"] = directory

        if action == "project_code":
            project_path = ""
            instruction = ""
            embedded = self._detect_embedded_project_command(text)
            if embedded:
                project_path = str(embedded["parameters"].get("project_path") or "").strip()
                instruction = str(embedded["parameters"].get("instruction") or "").strip()
            if not project_path:
                directory = self._extract_directory(text)
                normalized_directory = self._normalize_query_text(directory or "")
                if normalized_directory in {"this project", "the project", "this codebase", "the codebase", "project"}:
                    directory = ""
                target = self._extract_target_name(text)
                normalized_target = self._normalize_query_text(target or "")
                if normalized_target in {"this project", "the project", "this codebase", "the codebase", "project"}:
                    target = ""
                elif any(normalized_target.startswith(prefix) for prefix in {"this project ", "the project ", "this codebase ", "the codebase "}):
                    target = ""
                elif re.search(r"\b(?:add|update|change|modify|fix|create|build|generate)\b", normalized_target):
                    target = ""
                if self._contains_any(tokens, {"folder", "directory", "project"}):
                    project_path = directory or target
                elif target and directory:
                    project_path = f"{directory}/{target}"
                elif target:
                    project_path = target
            if not project_path:
                project_path = str(context.get("last_project_root") or "").strip()
            if not instruction:
                update_match = re.search(
                    r"\b(?:to|so that|with)\s+(.+)$",
                    text,
                    flags=re.IGNORECASE,
                )
                if update_match:
                    instruction = update_match.group(1).strip(" .")
            if project_path:
                params["project_path"] = project_path
            if instruction:
                params["instruction"] = instruction

        return params

    def _refers_to_previous_target(self, tokens):
        token_set = set(tokens)
        return bool(token_set & {"it", "that", "there", "them", "this"})

    def _extract_entity_after_verbs(self, text, verbs):
        pattern = r"\b(?:%s)\s+(.+)$" % "|".join(re.escape(verb) for verb in verbs)
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        entity = match.group(1).strip(" .")
        entity = re.sub(r"\bas\s+administrator\b", "", entity, flags=re.IGNORECASE).strip(" .")
        entity = re.sub(r"\bin\s+(brave|chrome|edge|firefox)\b.*$", "", entity, flags=re.IGNORECASE).strip(" .")
        entity = re.sub(
            r"\b(?:and|to)\s+(?:click|take|capture|record|start)\b.*$",
            "",
            entity,
            flags=re.IGNORECASE,
        ).strip(" .")
        return entity or None

    def _extract_browser(self, text):
        match = re.search(r"\bin\s+(brave|chrome|edge|firefox|brave browser|google chrome)\b", text, flags=re.IGNORECASE)
        return match.group(1).strip().lower() if match else None

    def _extract_interface_style(self, text):
        lowered = self._normalize_query_text(text)
        style_aliases = {
            "waves": {"wave", "waves", "ascii waves", "ocean"},
            "bubble": {"bubble", "sphere"},
        }
        target_match = re.search(
            r"\b(?:to|into)\s+(waves?|bubble)\b",
            lowered,
            flags=re.IGNORECASE,
        )
        if target_match:
            candidate = target_match.group(1).strip().lower()
            candidate = "waves" if candidate in {"wave", "waves"} else candidate
            if candidate in style_aliases:
                return candidate
        for style, aliases in style_aliases.items():
            if any(alias in lowered for alias in aliases):
                return style
        return ""

    def _extract_application_target(self, text):
        lowered = self._normalize_query_text(text)
        aliases = [
            "vlc media player",
            "visual studio code",
            "windows powershell",
            "file explorer",
            "brave browser",
            "google chrome",
            "microsoft edge",
            "camera app",
            "vs code",
            "versus code",
            "powershell",
            "terminal",
            "camera",
            "blender",
            "matlab",
            "spotify",
            "chrome",
            "firefox",
            "brave",
            "edge",
            "vlc",
        ]
        for alias in aliases:
            if re.search(rf"\b(?:in|using|with)\s+(?:the\s+)?{re.escape(alias)}\b", lowered):
                return alias
        return None

    def _extract_setting_name(self, text):
        phrases = [
            "ascii waves",
            "bubble interface",
            "bubble",
            "wave style",
            "waves",
            "wave",
            "wake word sensitivity",
            "wake sensitivity",
            "wake word response",
            "wake response",
            "call response",
            "summon response",
            "voice model",
            "voice preset",
            "voice selection",
            "voice authentication",
            "voice auth",
            "airplane mode",
            "night light",
            "energy saver",
            "screen saver",
            "screensaver",
            "microphone",
            "volume",
            "sound",
            "brightness",
            "bluetooth",
            "wifi",
            "vpn",
            "display",
        ]
        lowered = str(text or "").lower()
        for phrase in phrases:
            if phrase in lowered:
                return phrase
        return ""

    def _extract_news_topic(self, text):
        lowered = self._normalize_query_text(text)
        if not lowered:
            return ""
        match = re.search(r"\b(?:about|on|in|for|regarding)\s+([a-z0-9 ._-]+)$", lowered)
        topic = match.group(1).strip(" .") if match else ""
        if not topic:
            return ""
        noise = {
            "news",
            "headlines",
            "headline",
            "trending",
            "trend",
            "trends",
            "latest",
            "today",
            "todays",
            "today's",
            "new",
            "updates",
            "update",
        }
        cleaned = " ".join(token for token in topic.split() if token not in noise).strip()
        return cleaned or topic

    def _parse_number_words(self, words):
        units = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "thirteen": 13,
            "fourteen": 14,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
        }
        tens = {
            "twenty": 20,
            "thirty": 30,
            "forty": 40,
            "fifty": 50,
            "sixty": 60,
            "seventy": 70,
            "eighty": 80,
            "ninety": 90,
        }
        if not words:
            return None
        total = 0
        current = 0
        consumed = False
        for token in words:
            if token in units:
                current += units[token]
                consumed = True
            elif token in tens:
                current += tens[token]
                consumed = True
            elif token == "hundred":
                current = max(1, current) * 100
                consumed = True
            elif token in {"and", "-"}:
                continue
            else:
                return None
        if not consumed:
            return None
        total += current
        return total

    def _extract_percentage_value(self, text):
        lowered = str(text or "").lower()
        digit_match = re.search(r"\b(\d{1,3})\s*(?:percent|%)?\b", lowered)
        if digit_match:
            return max(0, min(100, int(digit_match.group(1))))

        words_pattern = (
            r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
            r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
            r"sixty|seventy|eighty|ninety|hundred)(?:[\s-]+(?:and\s+)?"
            r"(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
            r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
            r"sixty|seventy|eighty|ninety|hundred))*\b"
        )
        match = re.search(words_pattern, lowered)
        if not match:
            return None
        phrase = match.group(0).replace("-", " ")
        words = [part for part in phrase.split() if part]
        value = self._parse_number_words(words)
        if value is None:
            return None
        return max(0, min(100, int(value)))

    def _extract_requested_name(self, text):
        patterns = [
            r"\bchange\s+(?:your|assistant)\s+name\s+to\s+([A-Za-z0-9 _-]+)$",
            r"\bset\s+(?:your|assistant)\s+name\s+to\s+([A-Za-z0-9 _-]+)$",
            r"\bcall\s+(?:yourself|you)\s+([A-Za-z0-9 _-]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = " ".join(match.group(1).strip(" .!?").split())
                if value:
                    return value
        return None

    def _strip_usage_context(self, value):
        return re.split(
            r"\s+\b(?:in|using|with|on)\b\s+(?=(?:the\s+)?(?:youtube music|youtube|spotify|vlc media player|visual studio code|vs code|windows powershell|file explorer|brave browser|google chrome|microsoft edge|camera app|camera|blender|matlab|vlc|brave|chrome|edge|firefox|powershell|terminal|browser|app|application)\b)",
            str(value or "").strip(),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

    def _clean_directory_reference(self, value):
        cleaned = str(value or "").strip().strip("\"'")
        if not cleaned:
            return None
        lowered = self._normalize_query_text(cleaned)
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
        cleaned = re.sub(r"\b(?:directory|folder|path)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        return cleaned or None

    def _clean_target_reference(self, value):
        cleaned = str(value or "").strip().strip("\"'")
        if not cleaned:
            return None
        cleaned = self._strip_usage_context(cleaned)
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^(?:file|folder|directory|app|application|program)\s+(?:named|called)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:named|called)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:which|that)\s+is$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"\b(?:which|that)$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"\b(?:file|folder|directory|app|application|program)\b$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        return cleaned or None

    def _extract_directory(self, text):
        match = re.search(
            r"\b(?:in|inside|under|at|from)\s+(?:the\s+)?([A-Za-z0-9_ ./\\:-]+?)(?=\s+\b(?:with|write|and|to|from|called|named|using|on|open|launch|run|start|read|delete|create|make|copy|move|rename|duplicate|list|show|display|update|modify|change|edit|fix|rewrite|refactor|implement|add)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = self._clean_directory_reference(match.group(1))
        if not value:
            return value
        if value.lower() in {"text mode", "voice mode"} or value.lower().startswith(("which ", "that ", "where ")):
            return None
        if value.lower() in {"which", "that", "where", "file", "folder", "directory", "path"}:
            return None
        lower_value = value.lower()
        if (" file" in lower_value or lower_value.endswith("file")) and (
            re.search(r"\.[a-z0-9]{1,8}\b", lower_value) or " dot " in lower_value
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
        lowered = value.lower()
        if lowered in language_tokens:
            return None
        if any(lowered.startswith(f"{token} ") for token in language_tokens):
            if re.search(r"\bin\s+(?:python|py|cpp|c\+\+|java|javascript|typescript|text|json|yaml|markdown)\b", text, flags=re.IGNORECASE):
                return None
        return value

    def _extract_list_directory_target(self, text):
        lowered = str(text or "").lower()
        if any(phrase in lowered for phrase in {"current directory", "current folder", "this directory", "this folder", "here"}):
            return ""
        match = re.search(
            r"\b(?:list|show|display)\s+(?:the\s+)?(?:contents|files)?(?:\s+(?:of|inside))?\s+(?:the\s+)?([A-Za-z0-9_ ./\\:-]+?)(?=\s*$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1).strip(" .")
        value = re.sub(r"^(?:contents\s+(?:of|inside)|files\s+(?:in|inside))\s+", "", value, flags=re.IGNORECASE)
        value = self._clean_directory_reference(value)
        if not value:
            return ""
        return value or None

    def _extract_tree_directory_target(self, text):
        lowered = str(text or "").lower()
        if any(phrase in lowered for phrase in {"current directory", "current folder", "folder i am in", "folder i am inside", "the folder i am in", "the folder i am inside"}):
            return ""
        patterns = [
            r"\b(?:draw|show|display)\s+(?:the\s+)?(?:file|folder|directory|system)?\s*(?:tree|structure)\s+(?:of|for|inside)\s+(?:the\s+)?(.+)$",
            r"\b(?:file|folder|directory|system)\s+(?:tree|structure)\s+(?:of|for|inside)\s+(?:the\s+)?(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = self._clean_directory_reference(match.group(1).strip(" ."))
            if value is not None:
                return value
        return None

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
            r"\b(?:file|folder|directory|app|application|program)\s+(?:named|called)\s+(.+?)(?=\s*(?:,|;|$|\b(?:in|inside|under|from|to|with|using|and|on|update|modify|change|edit)\b))",
            text,
            flags=re.IGNORECASE,
        )
        if explicit:
            raw = explicit.group(1).strip(" .")
            raw = re.split(r"\s*,\s*", raw, maxsplit=1)[0]
            return self._clean_target_reference(raw)
        match = re.search(
            r"\b(?:open|run|launch|start|play|read|delete|remove|modify|edit|create|make|copy|move|rename|duplicate|list|show|display)\s+(.+?)(?=\s+\b(?:in|inside|under|from|to|with|using|on|and|named|called)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return self._clean_target_reference(match.group(1))

    def _extract_filename(self, text, lowered):
        lowered = re.sub(r"\bunderscore\b", "_", lowered, flags=re.IGNORECASE)
        lowered = re.sub(r"\b(?:hyphen|dash)\b", "-", lowered, flags=re.IGNORECASE)
        lowered = re.sub(r"\s*([_.-])\s*", r"\1", lowered)
        explicit = re.search(r"\b([A-Za-z0-9_\-]+(?:\.[A-Za-z0-9]+)+)\b", lowered) or re.search(
            r"\b([A-Za-z0-9_\-]+(?:\.[A-Za-z0-9]+)+)\b",
            text,
        )
        if explicit:
            name = explicit.group(1)
            return name[:-3] + ".py" if name.lower().endswith(".pi") else name
        spoken = re.search(r"\b([A-Za-z0-9_\- ]+?)\s+dot\s+([A-Za-z0-9]+)\b", lowered, flags=re.IGNORECASE)
        if spoken:
            stem = spoken.group(1).strip()
            while True:
                cleaned = re.sub(
                    r"^(?:create|make|generate|write|a|an|the|file|script|program|document|named|called)\s+",
                    "",
                    stem,
                    flags=re.IGNORECASE,
                )
                if cleaned == stem:
                    break
                stem = cleaned
            stem = stem.replace(" ", "_")
            extension = spoken.group(2).strip().lower()
            if extension == "pi":
                extension = "py"
            return f"{stem}.{extension}"
        name_match = re.search(r"\b(?:file|script|program|document)\s+(?:named|called)?\s*([A-Za-z0-9_\- .]+)\b", lowered, flags=re.IGNORECASE)
        if name_match:
            stem = name_match.group(1).strip(" .")
            stem = re.sub(r"^(?:create|make|generate|write)\s+", "", stem, flags=re.IGNORECASE)
            stem = re.sub(r"\b(?:in|inside|under|with|that|which)\b.*$", "", stem, flags=re.IGNORECASE).strip(" .")
            extension = self._infer_extension(lowered)
            if stem and extension:
                return f"{stem.replace(' ', '_')}{extension}"
        return None

    def _infer_extension(self, lowered):
        for hint, extension in self.FILE_HINTS.items():
            if re.search(rf"\b{re.escape(hint)}\b", lowered):
                return extension
        if "text file" in lowered:
            return ".txt"
        return None

    def _default_filename_for_request(self, text, lowered):
        extension = self._infer_extension(lowered) or ".txt"
        request = self._extract_content_request(text) or ""
        request_lower = request.lower()
        keyword_names = (
            ("palindrome", "palindrome_checker"),
            ("ascii", "ascii_art"),
            ("reverse", "reverse_number"),
            ("fibonacci", "fibonacci"),
            ("prime", "prime_checker"),
            ("sort", "sorting"),
            ("weather", "weather_lookup"),
            ("calculator", "calculator"),
            ("video", "video_processor"),
            ("audio", "audio_processor"),
        )
        for keyword, stem in keyword_names:
            if keyword in request_lower:
                return f"{stem}{extension}"

        if re.search(r"\b(?:named|called)\b", text, flags=re.IGNORECASE):
            target = self._extract_target_name(text)
            if target:
                candidate = re.sub(r"\b(?:file|script|program|document)\b", "", target, flags=re.IGNORECASE).strip(" .")
                candidate = re.sub(r"\s+", "_", candidate)
                if candidate and "." not in candidate:
                    return f"{candidate}{extension}"
        informative_words = []
        stop_words = {
            "create",
            "make",
            "generate",
            "write",
            "code",
            "program",
            "script",
            "file",
            "that",
            "which",
            "with",
            "from",
            "inside",
            "directory",
            "folder",
            "user",
            "given",
            "check",
            "string",
            "input",
            "output",
            "the",
            "a",
            "an",
            "to",
            "for",
            "of",
            "in",
            "on",
            "and",
            "or",
            "is",
            "are",
            "be",
            "if",
            "it",
        }
        for token in re.findall(r"[a-z0-9_]+", request_lower or lowered):
            if token in stop_words or token.isdigit() or len(token) < 3:
                continue
            informative_words.append(token)
            if len(informative_words) >= 3:
                break
        stem = "_".join(informative_words) if informative_words else "notes"
        return f"{stem}{extension}"

    def _extract_literal_content(self, text):
        quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
        if quoted:
            return quoted[-1]
        match = re.search(r"\b(?:with|saying|text|content)\s+(.+)$", text, flags=re.IGNORECASE)
        if match:
            content = match.group(1).strip()
            if len(content.split()) <= 12 and not any(marker in content.lower() for marker in ("code", "program", "function", "script")):
                return content
        return None

    def _extract_content_request(self, text):
        patterns = [
            r"\b(?:update|modify|change)\s+(?:the\s+)?code\s+(?:to|so that)\s+(.+)$",
            r"\b(?:update|modify|change|edit)\s+(?:the\s+)?(?:file|script|program|code)\s+(?:named|called)?\s+.+?\s+\bto\b\s+(.+)$",
            r"\b(?:update|modify|change|edit)\s+.+?\s+\bto\b\s+(.+)$",
            r"\band\s+write\s+(.+)$",
            r"\bin\s+which\s+write\s+(.+)$",
            r"\bin\s+which\s+(.+)$",
            r"\bwhich\s+(.+)$",
            r"\bwrite\s+code\s+to\s+(.+)$",
            r"\bprogram\s+to\s+(.+)$",
            r"\bwrite\s+(.+)$",
            r"\bcontaining\s+(.+)$",
            r"\bthat\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                request = match.group(1).strip(" .")
                if request:
                    return request
        return None

    def _extract_song_request(self, lowered):
        platform = ""
        if "youtube music" in lowered:
            platform = "youtube_music"
        elif "spotify" in lowered:
            platform = "spotify"
        elif "youtube" in lowered:
            platform = "youtube"

        working = lowered.strip(" .")
        match = re.search(r"\b(?:play|open|run|start)\s+(.+)$", working)
        song = match.group(1).strip(" .") if match else working
        song = re.sub(r"\b(?:on|in|using|with)\s+(youtube music|youtube|spotify)\b.*$", "", song, flags=re.IGNORECASE).strip(" .")
        song = song.replace("three blue one brown", "3blue1brown")
        song = re.sub(r"\b(?:song|music|video)\b$", "", song, flags=re.IGNORECASE).strip(" .")
        return song, platform

    def _extract_conversation_id(self, text):
        raw = str(text or "").strip()
        match = re.search(r"\b(?:chat|conversation)\s+([a-z]?\d{1,6})\b", raw, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"\b([cC]\d{1,6})\b", raw)
        if not match:
            match = re.search(r"\b(\d{1,6})\b", raw)
        if not match:
            return ""
        token = match.group(1).strip().upper()
        if token.startswith("C"):
            digits = token[1:]
        else:
            digits = token
        if not digits.isdigit():
            return ""
        return f"C{int(digits):04d}"

    def _split_name_and_directory(self, text):
        value = str(text or "").strip(" .")
        if not value:
            return "", ""
        match = re.search(
            r"(.+?)\s+\b(?:which|that)\s+is\s+(?:inside|in|under)\s+(.+)$",
            value,
            flags=re.IGNORECASE,
        ) or re.search(
            r"(.+?)\s+\b(?:inside|in|under)\s+(.+)$",
            value,
            flags=re.IGNORECASE,
        )
        if not match:
            return value, ""
        name = self._clean_target_reference(match.group(1)) or ""
        directory = self._clean_directory_reference(match.group(2)) or ""
        return name, directory

    def _extract_transfer_params(self, text, context):
        params = {}
        move_match = re.search(r"\b(?:copy|move|paste)\s+(.+?)\s+from\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
        if move_match:
            source_name, embedded_dir = self._split_name_and_directory(move_match.group(1))
            params["name"] = source_name or move_match.group(1).strip(" .")
            params["source_dir"] = embedded_dir or self._clean_directory_reference(move_match.group(2)) or move_match.group(2).strip(" .")
            params["destination"] = self._clean_directory_reference(move_match.group(3)) or move_match.group(3).strip(" .")
            return params
        simple_move = re.search(r"\b(?:copy|move|paste|duplicate)\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
        if simple_move:
            source_name, source_dir = self._split_name_and_directory(simple_move.group(1))
            params["name"] = source_name or simple_move.group(1).strip(" .")
            if source_dir:
                params["source_dir"] = source_dir
            params["destination"] = self._clean_directory_reference(simple_move.group(2)) or simple_move.group(2).strip(" .")
            return params
        rename_match = re.search(r"\brename\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
        if rename_match:
            params["name"] = rename_match.group(1).strip(" .")
            params["new_name"] = rename_match.group(2).strip(" .")
            return params
        duplicate_match = re.search(r"\bduplicate\s+(.+)$", text, flags=re.IGNORECASE)
        if duplicate_match:
            params["name"] = duplicate_match.group(1).strip(" .")
            return params
        if self._refers_to_previous_target(self._tokenize(self._normalize_query_text(text))) and context.get("last_path"):
            params["path"] = context["last_path"]
        return params

    def _extract_website_target(self, text):
        lowered = self._normalize_query_text(text)
        lowered = lowered.replace(" slash ", "/")
        domain_match = re.search(r"\b((?:https?://)?(?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:/[^\s]*)?)\b", lowered)
        if domain_match:
            candidate = domain_match.group(1)
            extension = candidate.rsplit(".", 1)[-1].lower() if "." in candidate else ""
            file_like_extensions = {
                "txt",
                "md",
                "json",
                "yaml",
                "yml",
                "csv",
                "xml",
                "py",
                "js",
                "ts",
                "tsx",
                "jsx",
                "cpp",
                "c",
                "h",
                "hpp",
                "java",
                "go",
                "rs",
                "rb",
                "php",
                "html",
                "css",
                "mp3",
                "wav",
                "flac",
                "mp4",
                "mkv",
                "avi",
                "mov",
                "webm",
                "wmv",
                "png",
                "jpg",
                "jpeg",
                "gif",
                "pdf",
            }
            if extension in file_like_extensions and "http" not in candidate and "www." not in candidate:
                return None
            return candidate
        if any(site in lowered for site in self.WEBSITE_WORDS):
            match = re.search(r"\b(?:open|show|launch|start)\s+(.+)$", lowered)
            return match.group(1).strip(" .") if match else lowered
        return None

    def _llm_intent_fallback(self, text, context):
        if len(text.split()) < 2:
            return None
        prompt = (
            "You are an intent classifier for a desktop assistant.\n"
            f"User input: {text}\n"
            f"Recent context: {context}\n"
            "If the user is asking for a desktop/system command, respond with only JSON using this shape:\n"
            '{"intent":"system_command","action":"<valid_action>","parameters":{"raw_text":"<original text>"}}\n'
            f"Valid actions: {', '.join(sorted(self.VALID_ACTIONS))}\n"
            "If it is not a desktop/system command, respond with exactly NO_COMMAND."
        )
        try:
            response = str(self.llm.generate(prompt)).strip()
            if response == "NO_COMMAND" or "NO_COMMAND" in response:
                return None
            data = self.llm._extract_json(response)
            action = str(data.get("action") or "").strip()
            if data.get("intent") != "system_command" or action not in self.VALID_ACTIONS:
                return None
            normalized = self._normalize_query_text(text)
            tokens = self._correct_tokens(self._tokenize(normalized))
            params = self._extract_params(text, normalized, tokens, action, context)
            llm_params = data.get("parameters") or {}
            if isinstance(llm_params, dict):
                for key, value in llm_params.items():
                    params.setdefault(key, value)
            return {"intent": "system_command", "action": action, "parameters": params}
        except Exception as exc:
            print(f"LLM intent fallback failed: {exc}")
            return None
