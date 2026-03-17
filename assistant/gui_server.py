import json
import os
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from http.server import HTTPServer

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


class AssistantRuntime:
    def __init__(self):
        self.config = AssistantConfig()
        self.llm = LLMEngine(online=True)
        self.llm.humor_level = int(self.config.get("humor_level", 50))
        self.intent_engine = IntentEngine(self.llm)
        self.system = SystemActions(base_dir=os.getcwd(), llm=self.llm)
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
        self.attachments = []
        self.model_preference = str(self.config.get("model_preference") or "auto").strip().lower()
        self.access_level = str(self.config.get("access_level") or "full").strip().lower()
        self.lock = threading.Lock()

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

    def _generate_with_model(self, prompt):
        preference = (self.model_preference or "auto").lower()
        if preference == "groq":
            return str(self.llm.groq_generate(prompt, model=self.llm.groq_model))
        if preference == "openrouter":
            return str(self.llm.cloud_generate(prompt, model=self.llm.openrouter_model))
        if preference == "ollama":
            return str(self.llm.local_generate(prompt, model=self.llm.local_model))
        if preference == "groq-code":
            return str(self.llm.groq_generate(prompt, model=self.llm.groq_code_model, system_prompt=self.llm._build_code_system_prompt()))
        if preference == "openrouter-code":
            return str(self.llm.cloud_generate(prompt, model=self.llm.openrouter_code_model, system_prompt=self.llm._build_code_system_prompt()))
        if preference == "ollama-code":
            return str(self.llm.local_generate(prompt, model=self.llm.local_code_model, system_prompt=self.llm._build_code_system_prompt()))
        return str(self.llm.generate(prompt))

    def _handle_control_action(self, action, params):
        if action == "change_voice":
            return self._apply_voice_preset(params.get("preset"))
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

    def handle(self, text, confirm=None, model_preference=None, access_level=None, attachments=None):
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

            if self.pending_action is not None and confirm is not None:
                action, params = self.pending_action
                if confirm:
                    if not self._permission_allows(action):
                        self.pending_action = None
                        self.pending_prompt = ""
                        return {
                            "response": f"Blocked by {self.access_level} access mode. Update permissions to proceed.",
                            "needs_confirmation": False,
                        }
                    result = self.system.execute(action, params)
                    self.pending_action = None
                    self.pending_prompt = ""
                    return {"response": result, "needs_confirmation": False}
                self.pending_action = None
                self.pending_prompt = ""
                return {"response": "Cancelled.", "needs_confirmation": False}

            intent_data = self.intent_engine.detect(text, context=self.system.session_context)
            intent = intent_data.get("intent", "conversation")
            action = intent_data.get("action")
            params = intent_data.get("parameters", {})

            if intent == "system_command" and action:
                control_result = self._handle_control_action(action, params)
                if control_result:
                    return {"response": control_result, "needs_confirmation": False}

                if not self._permission_allows(action):
                    return {
                        "response": f"Blocked by {self.access_level} access mode. Update permissions to proceed.",
                        "needs_confirmation": False,
                    }

                if action in CONFIRM_ACTIONS:
                    target = self.system.describe_target(params)
                    prompt = f"Confirm {action.replace('_', ' ')} for {target}?"
                    self.pending_action = (action, params)
                    self.pending_prompt = prompt
                    return {"response": prompt, "needs_confirmation": True}

                result = self.system.execute(action, params)
                return {"response": result, "needs_confirmation": False}

            context = self.memory.get_context()
            attachment_context = self._summarize_attachments()
            if attachment_context:
                prompt = f"Attached context:\n{attachment_context}\n\n{context}\nUser: {text}\nAssistant:".strip()
            else:
                prompt = f"{context}\nUser: {text}\nAssistant:".strip()

            try:
                response = self._generate_with_model(prompt).strip()
            except Exception:
                response = ""
            if not response:
                response = "I could not generate a response."
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
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/command":
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
        confirm_flag = data.get("confirm")
        model_preference = data.get("model")
        access_level = data.get("access_level")
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
        )
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
