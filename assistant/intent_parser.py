from .intent_engine import IntentEngine


class IntentParser:
    def __init__(self, llm_engine=None):
        self.engine = IntentEngine(llm_engine) if llm_engine is not None else None

    def parse(self, text):
        if self.engine is None:
            return "conversation", {"raw_text": text}

        result = self.engine.detect(text)
        if result.get("intent") != "system_command":
            return "conversation", result.get("parameters", {})
        return result.get("action"), result.get("parameters", {})
