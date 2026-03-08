import re
from collections import defaultdict
from difflib import get_close_matches

from .config import VOICE_PRESETS


class IntentEngine:
    VALID_ACTIONS = {
        "shutdown_system",
        "restart_system",
        "sleep_system",
        "open_application",
        "open_path",
        "close_application",
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
        "set_wifi",
        "set_bluetooth",
        "set_airplane_mode",
        "set_night_light",
        "eject_drive",
        "run_as_admin",
        "play_music",
        "draw_file_tree",
        "reset_password",
        "restart_setup",
        "change_assistant_name",
        "clear_history",
        "read_terminal",
        "set_voice_auth",
        "set_humor",
        "change_language",
        "change_voice",
        "set_autostart",
        "enter_text_mode",
        "project_code",
    }

    ACTION_WORDS = {
        "open": {"open", "launch", "run", "start", "show"},
        "create": {"create", "make", "generate", "build", "write", "new"},
        "delete": {"delete", "remove", "erase", "trash"},
        "read": {"read", "show", "display", "view"},
        "modify": {"edit", "modify", "update", "append", "overwrite", "replace", "change"},
        "copy": {"copy", "clone"},
        "move": {"move", "transfer", "shift", "relocate"},
        "rename": {"rename"},
        "duplicate": {"duplicate"},
        "switch": {"set", "switch", "turn", "enable", "disable", "activate", "deactivate", "change"},
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
    WEBSITE_WORDS = {"youtube", "github", "spotify", "reddit", "gmail", "chatgpt", "google"}
    APP_WORDS = {
        "brave",
        "browser",
        "blender",
        "vscode",
        "code",
        "camera",
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
    }
    FOLDER_WORDS = {
        "folder",
        "directory",
        "downloads",
        "documents",
        "pictures",
        "videos",
        "desktop",
        "music",
        "recycle",
        "bin",
    }
    SETTING_WORDS = {"wifi", "bluetooth", "airplane", "brightness", "night", "light"}
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
        self.vocabulary.update({"project", "channel", "profile", "slash", "percent", "setup", "history", "terminal", "name"})

    def detect(self, text, context=None):
        original = str(text or "").strip()
        if not original:
            return {"intent": "conversation", "parameters": {}}

        if original.startswith("@"):
            return self._detect_project_command(original)

        normalized = self._normalize_query_text(original)
        tokens = self._correct_tokens(self._tokenize(normalized))
        action = self._detect_action(original, normalized, tokens)

        if action:
            return {
                "intent": "system_command",
                "action": action,
                "parameters": self._extract_params(original, normalized, tokens, action, context or {}),
            }

        llm_result = self._llm_intent_fallback(original, context or {})
        if llm_result:
            return llm_result

        return {"intent": "conversation", "parameters": {"raw_text": original}}

    def _detect_project_command(self, text):
        body = text[1:].strip()
        if not body:
            return {"intent": "conversation", "parameters": {"raw_text": text}}
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
            ("dot pi", "dot py"),
            (" dot py ", ".py "),
            ("c plus plus", "cpp"),
            ("control space", "ctrl space"),
            ("text interface", "text mode"),
            ("humour", "humor"),
            ("forward slash", "/"),
            (" slash ", "/"),
            (" dot ", "."),
        )
        for source, target in replacements:
            normalized = normalized.replace(source, target)
        normalized = re.sub(r"(\d+)\s*%", r"\1 percent", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _tokenize(self, normalized):
        return re.findall(r"[a-z0-9@./\\:+_-]+", normalized)

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
        if self._contains_any(tokens, {"file", "document", "script", "program", "code"}):
            return True
        if any(hint in normalized for hint in ("text file", "python file", "cpp file")):
            return True
        return False

    def _is_folder_context(self, tokens):
        return self._contains_any(tokens, self.FOLDER_WORDS)

    def _is_website_context(self, normalized, tokens):
        if self._contains_any(tokens, self.WEBSITE_WORDS | {"channel", "profile", "website", "site"}):
            return True
        if any(token.startswith("http") or "/" in token or "." in token for token in tokens):
            return True
        if "youtube/" in normalized or "github/" in normalized or "spotify/" in normalized:
            return True
        return False

    def _detect_action(self, text, normalized, tokens):
        scores = defaultdict(int)
        token_set = set(tokens)

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
        if "terminal" in token_set and self._contains_any(tokens, {"read", "aloud", "say", "speak"}):
            scores["read_terminal"] += 15
        if "voice" in token_set and self._contains_any(tokens, {"auth", "authentication", "verification", "verify"}):
            scores["set_voice_auth"] += 15
        if "humor" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["set_humor"] += 12
        if self._contains_any(tokens, {"language", "conversation"}) and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["change_language"] += 12
        if "voice" in token_set and "auth" not in token_set and self._contains_any(tokens, {"preset"} | self.ACTION_WORDS["switch"]):
            scores["change_voice"] += 10
            for preset in VOICE_PRESETS:
                if preset in token_set:
                    scores["change_voice"] += 4
        if "text" in token_set and "mode" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"open"}):
            scores["enter_text_mode"] += 14
        if self._contains_any(tokens, {"autostart", "startup", "login"}) and self._contains_any(tokens, self.ACTION_WORDS["switch"]):
            scores["set_autostart"] += 12

        if self._contains_any(tokens, {"shutdown", "power"}) or self._contains_phrase(normalized, {"turn off my computer", "turn off device"}):
            scores["shutdown_system"] += 14
        if self._contains_any(tokens, {"restart", "reboot"}):
            scores["restart_system"] += 14
        if self._contains_any(tokens, {"sleep", "suspend"}):
            scores["sleep_system"] += 14

        if "brightness" in token_set and (self._contains_any(tokens, self.ACTION_WORDS["switch"]) or "percent" in token_set):
            scores["set_brightness"] += 13
        if "wifi" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_wifi"] += 13
        if "bluetooth" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_bluetooth"] += 13
        if "airplane" in token_set and "mode" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_airplane_mode"] += 13
        if "night" in token_set and "light" in token_set and self._contains_any(tokens, self.ACTION_WORDS["switch"] | {"on", "off"}):
            scores["set_night_light"] += 11

        if self._contains_any(tokens, self.ACTION_WORDS["create"]):
            if self._is_file_context(normalized, tokens):
                scores["create_file"] += 15
            if self._is_folder_context(tokens):
                scores["create_folder"] += 13
        if self._contains_any(tokens, self.ACTION_WORDS["delete"]) and (self._is_file_context(normalized, tokens) or self._is_folder_context(tokens) or self._refers_to_previous_target(tokens)):
            scores["delete_path"] += 14
        if self._contains_any(tokens, self.ACTION_WORDS["read"]) and (self._is_file_context(normalized, tokens) or self._refers_to_previous_target(tokens)):
            scores["read_file"] += 12
        if self._contains_any(tokens, {"list", "browse", "contents"}) and (self._is_folder_context(tokens) or "files" in token_set):
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
        if self._contains_any(tokens, self.ACTION_WORDS["modify"]) and (self._is_file_context(normalized, tokens) or self._refers_to_previous_target(tokens)):
            scores["modify_file"] += 13

        if self._contains_any(tokens, self.ACTION_WORDS["play"]) and (self._contains_any(tokens, {"music", "song", "spotify", "youtube"}) or len(tokens) > 1):
            scores["play_music"] += 13
        if self._contains_any(tokens, {"tree", "structure"}) and self._contains_any(tokens, {"draw", "show", "system", "directory", "folder", "file"}):
            scores["draw_file_tree"] += 12

        if self._contains_any(tokens, self.ACTION_WORDS["close"]) and (self._contains_any(tokens, self.APP_WORDS | {"app", "application", "browser"}) or self._refers_to_previous_target(tokens)):
            scores["close_application"] += 12

        if self._contains_any(tokens, self.ACTION_WORDS["open"]):
            if self._is_website_context(normalized, tokens):
                scores["open_path"] += 15
            elif self._is_folder_context(tokens) or self._is_file_context(normalized, tokens) or self._refers_to_previous_target(tokens):
                scores["open_path"] += 13
            else:
                scores["open_application"] += 12
            if self._contains_any(tokens, self.APP_WORDS | {"app", "application", "browser"}):
                scores["open_application"] += 4

        if not scores:
            return None
        action, score = max(scores.items(), key=lambda item: item[1])
        return action if score >= 9 else None

    def _extract_params(self, text, normalized, tokens, action, context):
        params = {"raw_text": text}
        lowered = normalized.lower()

        if action in {"set_brightness", "set_humor", "set_voice_auth"}:
            match = re.search(r"(\d{1,3})\s*(?:percent)?", lowered)
            if match:
                value = max(0, min(100, int(match.group(1))))
                if action == "set_brightness":
                    params["percent"] = value
                elif action == "set_humor":
                    params["level"] = value
                else:
                    params["threshold"] = value
        if action == "set_brightness":
            if self._contains_any(tokens, {"increase", "raise", "brighter"}):
                params["direction"] = "up"
            elif self._contains_any(tokens, {"decrease", "reduce", "lower", "dim"}):
                params["direction"] = "down"

        if action in {"set_wifi", "set_bluetooth", "set_airplane_mode", "set_night_light", "set_autostart"}:
            params["on"] = not any(word in lowered for word in ("off", "disable", "deactivate"))
        if action == "set_voice_auth":
            if any(word in lowered for word in ("off", "disable", "deactivate")):
                params["threshold"] = 0
            elif any(word in lowered for word in ("enable", "activate", "on")) and "threshold" not in params:
                params["threshold"] = 50
        if action == "change_language":
            match = re.search(r"\b(?:to|in)\s+([a-z]+)\b", lowered)
            if match:
                params["language"] = match.group(1)
        if action == "change_voice":
            for preset in VOICE_PRESETS:
                if preset in tokens:
                    params["preset"] = preset
                    break
        if action == "change_assistant_name":
            name = self._extract_requested_name(text)
            if name:
                params["assistant_name"] = name

        if action == "play_music":
            match = re.search(r"\bplay\s+(.+?)(?:\s+on\s+([a-z]+))?$", lowered)
            if match:
                params["song"] = match.group(1).strip(" .")
                if match.group(2):
                    params["platform"] = match.group(2)

        if action in {"open_application", "close_application", "run_as_admin"}:
            entity = self._extract_entity_after_verbs(text, ("run", "open", "launch", "start", "close", "quit", "exit", "terminate", "kill"))
            if entity:
                params["application"] = entity
            elif self._refers_to_previous_target(tokens):
                previous = context.get("last_application")
                if previous:
                    params["application"] = previous

        if action in {"open_path", "read_file", "delete_path", "modify_file", "change_directory"}:
            name = self._extract_target_name(text)
            if name:
                params["name"] = name
            directory = self._extract_directory(text)
            if directory:
                params["directory"] = directory
            if self._refers_to_previous_target(tokens) and context.get("last_path"):
                params["path"] = context["last_path"]
                params.setdefault("name", context.get("last_name") or "")

        if action == "list_directory":
            directory = self._extract_directory(text) or self._extract_target_name(text)
            if directory:
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

        if action == "draw_file_tree":
            directory = self._extract_directory(text)
            if directory:
                params["directory"] = directory

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
        return entity or None

    def _extract_browser(self, text):
        match = re.search(r"\bin\s+(brave|chrome|edge|firefox|brave browser|google chrome)\b", text, flags=re.IGNORECASE)
        return match.group(1).strip().lower() if match else None

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

    def _extract_directory(self, text):
        match = re.search(
            r"\b(?:in|inside|under|at|from)\s+(?:the\s+)?([A-Za-z0-9_ ./\\:-]+?)(?=\s+\b(?:with|write|and|to|from|called|named)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1).strip(" .")
        if value.lower() in {"text mode", "voice mode"}:
            return None
        return value

    def _extract_target_name(self, text):
        quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
        if quoted:
            return quoted[0].strip()
        match = re.search(
            r"\b(?:open|read|delete|remove|modify|edit|create|make|copy|move|rename|duplicate|list|show|display)\s+(.+?)(?=\s+\b(?:in|inside|under|from|to|with|and|named|called)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1).strip(" .")
        value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^(?:file|folder|directory|app|application)\s+", "", value, flags=re.IGNORECASE)
        return value or None

    def _extract_filename(self, text, lowered):
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
        target = self._extract_target_name(text)
        if target:
            candidate = re.sub(r"\b(?:file|script|program|document)\b", "", target, flags=re.IGNORECASE).strip(" .")
            candidate = re.sub(r"\s+", "_", candidate)
            if candidate and "." not in candidate:
                return f"{candidate}{extension}"
        return f"notes{extension}"

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
            r"\band\s+write\s+(.+)$",
            r"\bwrite\s+code\s+to\s+(.+)$",
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

    def _extract_transfer_params(self, text, context):
        params = {}
        move_match = re.search(r"\b(?:copy|move)\s+(.+?)\s+from\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
        if move_match:
            params["name"] = move_match.group(1).strip(" .")
            params["source_dir"] = move_match.group(2).strip(" .")
            params["destination"] = move_match.group(3).strip(" .")
            return params
        simple_move = re.search(r"\b(?:copy|move)\s+(.+?)\s+to\s+(.+)$", text, flags=re.IGNORECASE)
        if simple_move:
            params["name"] = simple_move.group(1).strip(" .")
            params["destination"] = simple_move.group(2).strip(" .")
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
            return domain_match.group(1)
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
