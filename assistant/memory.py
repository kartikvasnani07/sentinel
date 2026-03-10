import json
import re
from datetime import datetime, timezone
from pathlib import Path


class Memory:
    def __init__(self, max_turns=10, history_path=None):
        self.buffer = []
        self.max_turns = max_turns
        self.history_path = Path(history_path or (Path.home() / ".assistant" / "history.json"))
        self._data = {"next_id": 1, "current_id": "", "conversations": {}}
        self._load()
        self._ensure_current_conversation()

    @staticmethod
    def _now_iso():
        return datetime.now(timezone.utc).isoformat()

    def _load(self):
        if not self.history_path.exists():
            return
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._data.update(payload)
            if not isinstance(self._data.get("conversations"), dict):
                self._data["conversations"] = {}
            if not isinstance(self._data.get("next_id"), int):
                self._data["next_id"] = 1
        except Exception as exc:
            print(f"[memory] Failed to load history: {exc}")

    def _save(self):
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[memory] Failed to save history: {exc}")

    def _new_conversation_id(self):
        value = max(1, int(self._data.get("next_id") or 1))
        self._data["next_id"] = value + 1
        return f"C{value:04d}"

    def _normalize_conversation_id(self, conversation_id):
        raw = str(conversation_id or "").strip().upper()
        if not raw:
            return ""
        match = re.search(r"(?:CHAT|CONVERSATION)?\s*([A-Z]?\d+)", raw)
        if not match:
            return raw
        token = match.group(1)
        if token.startswith("C"):
            digits = token[1:]
        else:
            digits = token
        if not digits.isdigit():
            return raw
        return f"C{int(digits):04d}"

    def _refresh_buffer(self):
        current = self._data["conversations"].get(self._data.get("current_id") or "", {})
        messages = current.get("messages") or []
        trimmed = messages[-self.max_turns :]
        self.buffer = [(str(item.get("role") or ""), str(item.get("text") or "")) for item in trimmed]

    def _ensure_current_conversation(self):
        current_id = str(self._data.get("current_id") or "")
        if current_id and current_id in self._data["conversations"]:
            self._refresh_buffer()
            return
        self.start_new_conversation(title="New conversation")

    def _derive_title(self, text):
        value = str(text or "").strip()
        value = re.sub(r"^@/?", "", value)
        value = re.sub(r"\s+", " ", value)
        if not value:
            return "New conversation"
        words = value.split()
        title = " ".join(words[:8]).strip()
        return title[:1].upper() + title[1:] if title else "New conversation"

    def current_conversation_id(self):
        return str(self._data.get("current_id") or "")

    def start_new_conversation(self, title="New conversation"):
        conversation_id = self._new_conversation_id()
        timestamp = self._now_iso()
        self._data["conversations"][conversation_id] = {
            "id": conversation_id,
            "title": str(title or "New conversation").strip() or "New conversation",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [],
        }
        self._data["current_id"] = conversation_id
        self.buffer = []
        self._save()
        return conversation_id

    def open_last_conversation(self):
        conversations = self.list_conversations()
        if not conversations:
            return ""
        current_id = self.current_conversation_id()
        latest_id = ""
        for row in conversations:
            if row["id"] != current_id:
                latest_id = row["id"]
                break
        if not latest_id:
            latest_id = conversations[0]["id"]
        self._data["current_id"] = latest_id
        self._refresh_buffer()
        self._save()
        return latest_id

    def list_conversations(self):
        rows = []
        current_id = self.current_conversation_id()
        for conversation_id, conversation in self._data["conversations"].items():
            messages = conversation.get("messages") or []
            rows.append(
                {
                    "id": conversation_id,
                    "title": str(conversation.get("title") or "Untitled"),
                    "updated_at": str(conversation.get("updated_at") or ""),
                    "created_at": str(conversation.get("created_at") or ""),
                    "message_count": len(messages),
                    "is_current": conversation_id == current_id,
                }
            )
        rows.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return rows

    def switch_to_conversation(self, conversation_id):
        normalized = self._normalize_conversation_id(conversation_id)
        if normalized not in self._data["conversations"]:
            return False, "Conversation not found."
        self._data["current_id"] = normalized
        self._refresh_buffer()
        self._save()
        return True, f"Opened conversation {normalized}."

    def delete_conversation(self, conversation_id):
        normalized = self._normalize_conversation_id(conversation_id)
        if normalized not in self._data["conversations"]:
            return False, "Conversation not found."

        del self._data["conversations"][normalized]
        if not self._data["conversations"]:
            new_id = self.start_new_conversation()
            return True, f"Deleted {normalized}. Started {new_id}."

        if self._data.get("current_id") == normalized:
            latest = self.list_conversations()[0]["id"]
            self._data["current_id"] = latest
        self._refresh_buffer()
        self._save()
        return True, f"Deleted conversation {normalized}."

    def rename_current_conversation(self, title):
        current = self._data["conversations"].get(self.current_conversation_id())
        if current is None:
            return False
        cleaned = str(title or "").strip()
        if not cleaned:
            return False
        current["title"] = cleaned
        current["updated_at"] = self._now_iso()
        self._save()
        return True

    def add(self, role, text):
        role_value = str(role or "").strip() or "user"
        text_value = str(text or "").strip()
        conversation_id = self.current_conversation_id()
        conversation = self._data["conversations"].get(conversation_id)
        if conversation is None:
            self._ensure_current_conversation()
            conversation_id = self.current_conversation_id()
            conversation = self._data["conversations"][conversation_id]

        conversation.setdefault("messages", []).append({"role": role_value, "text": text_value})
        conversation["updated_at"] = self._now_iso()
        if role_value == "user" and conversation.get("title", "").lower().startswith("new conversation"):
            conversation["title"] = self._derive_title(text_value)
        self._refresh_buffer()
        self._save()

    def clear(self):
        conversation = self._data["conversations"].get(self.current_conversation_id())
        if conversation is None:
            return
        conversation["messages"] = []
        conversation["updated_at"] = self._now_iso()
        self.buffer.clear()
        self._save()

    def clear_all(self):
        self._data = {"next_id": 1, "current_id": "", "conversations": {}}
        self.start_new_conversation()

    def last(self, role=None):
        for entry_role, entry_text in reversed(self.buffer):
            if role is None or entry_role == role:
                return entry_text
        return ""

    def get_context(self):
        return "\n".join(f"{role}: {text}" for role, text in self.buffer)
