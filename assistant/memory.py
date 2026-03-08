class Memory:
    def __init__(self, max_turns=10):
        self.buffer = []
        self.max_turns = max_turns

    def add(self, role, text):
        self.buffer.append((role, text))
        if len(self.buffer) > self.max_turns:
            self.buffer.pop(0)

    def clear(self):
        self.buffer.clear()

    def last(self, role=None):
        for entry_role, entry_text in reversed(self.buffer):
            if role is None or entry_role == role:
                return entry_text
        return ""

    def get_context(self):
        return "\n".join(f"{role}: {text}" for role, text in self.buffer)
