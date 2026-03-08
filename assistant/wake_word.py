import json
import os
import queue
import re
import time
from difflib import SequenceMatcher

import numpy as np
import sounddevice as sd
import vosk


class WakeWordDetector:
    def __init__(self, model_path, wake_word, wake_variants=None):
        print("Loading Vosk model from:", model_path)
        self.model = vosk.Model(model_path)
        self.sample_rate = 16000
        self.block_size = int(os.getenv("WAKE_WORD_BLOCK_SIZE", "1024"))
        self.mic_gain = float(os.getenv("WAKE_WORD_GAIN", "6.0"))
        self.match_threshold = float(os.getenv("WAKE_WORD_MATCH_THRESHOLD", "0.55"))
        self.q = queue.Queue()
        self.wake_word = self._normalize_name(wake_word)
        self.wake_variants = self._prepare_variants(self.wake_word, wake_variants)
        self._log_variants()

    def update_wake_word(self, wake_word, wake_variants=None):
        self.wake_word = self._normalize_name(wake_word)
        self.wake_variants = self._prepare_variants(self.wake_word, wake_variants)
        self._log_variants()

    def _log_variants(self):
        preview = sorted(self.wake_variants)[:30]
        print(f"Wake variants loaded: {len(self.wake_variants)}")
        print("Wake preview:", ", ".join(preview))

    @staticmethod
    def _normalize_name(name):
        text = str(name or "").lower().strip()
        text = text.replace("_", " ").replace("-", " ")
        return " ".join(text.split())

    @classmethod
    def _prepare_variants(cls, wake_word, wake_variants=None):
        base = cls._normalize_name(wake_word)
        generated = cls.build_wake_variants(base)
        provided = set()
        for item in wake_variants or []:
            normalized = cls._normalize_name(item)
            if normalized:
                provided.add(normalized)
        return generated | provided | {base, base.replace(" ", "")}

    @classmethod
    def build_wake_variants(cls, wake_word):
        base = cls._normalize_name(wake_word)
        words = [word for word in base.split() if word]
        compact = "".join(words) or base.replace(" ", "")
        variants = {base, compact}
        variants.update(words)
        variants.update(cls._phrase_variants(words, compact))

        frontier = {compact}
        rounds = 0
        while len(variants) < 120 and frontier and rounds < 4:
            next_frontier = set()
            for item in frontier:
                next_frontier.update(cls._single_edit_variants(item))
                next_frontier.update(cls._phrase_variants([item], item))
                if len(next_frontier) > 200:
                    break
            variants.update(next_frontier)
            frontier = {item for item in next_frontier if len(item) <= max(16, len(compact) + 4)}
            rounds += 1

        cleaned = set()
        for item in variants:
            normalized = cls._normalize_name(item)
            compact_normalized = normalized.replace(" ", "")
            if 3 <= len(compact_normalized) <= max(18, len(compact) + 4):
                cleaned.add(normalized)

        if len(cleaned) < 96:
            cleaned.update(cls._fallback_variants(compact))

        cleaned.add(base)
        cleaned.add(compact)
        return cleaned or {base}

    @classmethod
    def _phrase_variants(cls, words, compact):
        if not compact:
            return set()

        variants = {
            compact,
            compact + compact[-1],
            compact[:-1] if len(compact) > 3 else compact,
            f"hey {compact}",
            f"ok {compact}",
            f"okay {compact}",
        }

        if len(compact) > 4:
            variants.update(
                {
                    compact[:2] + " " + compact[2:],
                    compact[:3] + " " + compact[3:],
                    compact[:-2] + " " + compact[-2:],
                }
            )

        if words:
            variants.add(" ".join(words))
            variants.add("  ".join(words))
            variants.add("".join(words))
            variants.add(" ".join(word[: max(1, len(word) - 1)] for word in words))
            variants.add(" ".join(word + word[-1] for word in words if word))

        return {cls._normalize_name(item) for item in variants if cls._normalize_name(item)}

    @classmethod
    def _single_edit_variants(cls, compact):
        variants = set()
        phonetic_substitutions = {
            "a": {"aa", "ah", "ai", "ay", "e"},
            "e": {"ee", "eh", "i", "ay"},
            "i": {"ee", "ie", "y", "ai"},
            "o": {"oh", "oo", "u"},
            "u": {"oo", "uh", "yu"},
            "f": {"ph"},
            "ph": {"f"},
            "c": {"k", "s"},
            "k": {"c", "q"},
            "q": {"k"},
            "s": {"z"},
            "z": {"s"},
            "v": {"w"},
            "w": {"v"},
            "g": {"j"},
            "j": {"g"},
            "t": {"d"},
            "d": {"t"},
            "x": {"ks", "z"},
            "y": {"ie", "i", "ey"},
            "r": {"ar", "er"},
            "l": {"el"},
        }

        if compact.endswith("y") and len(compact) > 3:
            variants.update({compact[:-1] + "i", compact[:-1] + "ie", compact[:-1] + "ey"})
        if compact.endswith("day") and len(compact) > 4:
            variants.update({compact[:-3] + "dey", compact[:-3] + "dai", compact[:-2], compact[:-1]})
        if compact.endswith("er") and len(compact) > 4:
            variants.update({compact[:-1], compact + "r"})
        if compact.endswith("a") and len(compact) > 3:
            variants.update({compact + "h", compact[:-1] + "uh"})

        for index in range(len(compact)):
            if len(compact) > 3:
                variants.add(compact[:index] + compact[index + 1 :])
            variants.add(compact[:index] + compact[index] + compact[index:])
            if index < len(compact) - 1:
                swapped = list(compact)
                swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
                variants.add("".join(swapped))

        for source, replacements in phonetic_substitutions.items():
            if source not in compact:
                continue
            for replacement in replacements:
                variants.add(compact.replace(source, replacement))
                for match in re.finditer(re.escape(source), compact):
                    start, end = match.span()
                    variants.add(compact[:start] + replacement + compact[end:])

        for split_index in range(2, len(compact) - 1):
            variants.add(compact[:split_index] + " " + compact[split_index:])

        return variants

    @classmethod
    def _fallback_variants(cls, compact):
        variants = set()
        vowels = {"a", "e", "i", "o", "u", "y"}
        for index, char in enumerate(compact):
            if char in vowels:
                for replacement in vowels - {char}:
                    variants.add(compact[:index] + replacement + compact[index + 1 :])
            else:
                variants.add(compact[:index] + compact[index + 1 :])
                if index < len(compact) - 1:
                    variants.add(compact[:index] + compact[index + 1] + compact[index + 2 :])
                variants.add(compact[:index] + char + char + compact[index + 1 :])
        for split_index in range(1, len(compact)):
            variants.add(compact[:split_index] + " " + compact[split_index:])
        return variants

    def audio_callback(self, indata, frames, time_info, status):
        _ = frames, time_info
        if status:
            print(f"[wake-word] audio status: {status}")

        audio = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
        audio *= self.mic_gain
        audio = np.clip(audio, -32768, 32767).astype(np.int16)
        self.q.put(audio.tobytes())

    def fuzzy_match(self, text):
        transcript = self._normalize_name(text)
        if not transcript:
            return False

        words = transcript.split()
        filtered_words = [word for word in words if word not in {"hey", "hi", "ok", "okay", "yo", "the"}]
        candidates = {transcript.replace(" ", "")}
        if filtered_words:
            candidates.add("".join(filtered_words))
            candidates.update(filtered_words)
            for start in range(len(filtered_words)):
                for end in range(start + 1, min(len(filtered_words), start + 3) + 1):
                    candidates.add("".join(filtered_words[start:end]))
                    candidates.add(" ".join(filtered_words[start:end]))

        for variant in self.wake_variants:
            compact_variant = variant.replace(" ", "")
            if not compact_variant:
                continue
            for candidate in candidates:
                compact_candidate = candidate.replace(" ", "")
                if not compact_candidate:
                    continue
                if len(compact_candidate) < 4:
                    continue
                length_gap = abs(len(compact_candidate) - len(compact_variant))
                if length_gap <= max(2, len(compact_variant) // 3) and (
                    compact_variant in compact_candidate or compact_candidate in compact_variant
                ):
                    return True
                ratio = SequenceMatcher(None, compact_candidate, compact_variant).ratio()
                if length_gap <= max(3, len(compact_variant) // 2) and ratio >= self.match_threshold:
                    return True

        return False

    def _iter_result_texts(self):
        recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
        while True:
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                yield None
                continue

            if recognizer.AcceptWaveform(chunk):
                payload = json.loads(recognizer.Result())
                yield payload.get("text", "")
            else:
                payload = json.loads(recognizer.PartialResult())
                yield payload.get("partial", "")

    @staticmethod
    def _should_stop(stop_event=None, stop_events=None):
        if stop_event is not None and stop_event.is_set():
            return True
        for event in stop_events or ():
            if event is not None and event.is_set():
                return True
        return False

    def listen_for_wake_word(self, stop_event=None, timeout=None, stop_events=None):
        self.q = queue.Queue()
        started = time.time()

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            dtype="int16",
            channels=1,
            callback=self.audio_callback,
        ):
            for text in self._iter_result_texts():
                if self._should_stop(stop_event=stop_event, stop_events=stop_events):
                    return False
                if timeout is not None and (time.time() - started) >= timeout:
                    return False
                if text and self.fuzzy_match(text):
                    return True
