import io
import os
import re
import threading
import unicodedata
import wave
from collections import deque

import numpy as np
import requests
import sounddevice as sd
import torch
from faster_whisper import WhisperModel

from .voice_auth import _cosine_similarity, _extract_features


class VoiceEngine:
    def __init__(self, online=True, model_size="small"):
        self.online = bool(online)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.sample_rate = 16000

        self.deepgram_api_key = self._normalize_env("DEEPGRAM_API_KEY")
        self.deepgram_model = self._normalize_env("DEEPGRAM_MODEL") or "nova-2"
        self.deepgram_language = "en-US"
        self.deepgram_timeout_sec = self._parse_int_env("DEEPGRAM_TIMEOUT_SEC", 8)

        env_model = self._normalize_env("WHISPER_MODEL_SIZE")
        self.requested_model = env_model or model_size or "small"
        self.model_candidates = self._build_model_candidates(self.requested_model)
        self.whisper_beam_size = self._parse_int_env("WHISPER_BEAM_SIZE", 3)
        self.whisper_allow_download = self._parse_bool_env("WHISPER_ALLOW_DOWNLOAD", False)
        self.min_transcript_chars = self._parse_int_env("STT_MIN_TRANSCRIPT_CHARS", 2)
        self.min_alnum_chars = self._parse_int_env("STT_MIN_ALNUM_CHARS", 2)
        self.min_avg_logprob = self._parse_float_env("WHISPER_MIN_AVG_LOGPROB", -1.5)
        self.light_speaker_similarity = self._parse_float_env("SPEAKER_LIGHT_SIMILARITY", 0.36)
        self.strong_speaker_similarity = self._parse_float_env("SPEAKER_STRONG_SIMILARITY", 0.58)

        self.model = None
        self.active_local_model = None
        self._last_local_model_error = None
        self._local_load_attempted = False
        self._model_lock = threading.Lock()
        self._warmup_thread = None

    def _normalize_env(self, key):
        value = os.getenv(key)
        if value is None:
            return None
        value = value.strip().strip('"').strip("'")
        return value or None

    def _parse_int_env(self, key, default):
        raw = self._normalize_env(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _parse_bool_env(self, key, default):
        raw = os.getenv(key)
        if raw is None:
            return default
        value = str(raw).strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return default

    def _parse_float_env(self, key, default):
        raw = self._normalize_env(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _build_model_candidates(self, requested):
        configured_path = self._normalize_env("WHISPER_MODEL_PATH")
        candidates = []
        if configured_path:
            candidates.append(configured_path)
        if requested:
            candidates.append(requested)
        candidates.extend(["small", "base", "tiny"])

        seen = set()
        result = []
        for item in candidates:
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result

    def _ensure_local_model(self, allow_download=False):
        with self._model_lock:
            if self.model is not None:
                return True

            if self._local_load_attempted and not allow_download:
                return False

            self._local_load_attempted = True
            errors = []
            for candidate in self.model_candidates:
                try:
                    self.model = WhisperModel(
                        candidate,
                        device=self.device,
                        compute_type=self.compute_type,
                        local_files_only=True,
                    )
                    self.active_local_model = candidate
                    print(f"Loaded local Whisper model: {candidate}")
                    return True
                except Exception as exc:
                    errors.append(f"{candidate} (local-only): {exc}")

            if allow_download:
                for candidate in self.model_candidates:
                    try:
                        self.model = WhisperModel(
                            candidate,
                            device=self.device,
                            compute_type=self.compute_type,
                            local_files_only=False,
                        )
                        self.active_local_model = candidate
                        print(f"Loaded Whisper model after download: {candidate}")
                        return True
                    except Exception as exc:
                        errors.append(f"{candidate} (download): {exc}")

            self._last_local_model_error = " | ".join(errors) if errors else "Unknown model load error."
            return False

    def warmup_local_model_async(self, allow_download=None):
        if self.model is not None:
            return False

        if self._warmup_thread is not None and self._warmup_thread.is_alive():
            return False

        use_download = self.whisper_allow_download if allow_download is None else bool(allow_download)

        def _warmup():
            self._ensure_local_model(allow_download=use_download)

        self._warmup_thread = threading.Thread(target=_warmup, daemon=True, name="stt-model-warmup")
        self._warmup_thread.start()
        return True

    def record_audio(self, duration=7):
        audio = sd.rec(
            int(float(duration) * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()
        return audio.flatten()

    def record_until_silence(
        self,
        *,
        max_duration=20.0,
        silence_duration=1.1,
        min_duration=0.8,
        start_timeout=6.0,
        preroll_duration=0.35,
        block_duration=0.08,
        fast_start=False,
        prefill_audio=None,
        speaker_reference=None,
        speaker_mode="none",
        on_speech_start=None,
    ):
        block_size = max(512, int(self.sample_rate * block_duration))
        pre_roll = deque(maxlen=max(1, int(preroll_duration / block_duration)))
        captured = []
        speech_started = False
        silence_seconds = 0.0
        total_seconds = 0.0
        wait_seconds = 0.0
        energy_samples = []

        normalized_speaker_mode = str(speaker_mode or "none").strip().lower()
        if normalized_speaker_mode not in {"none", "light", "strong"}:
            normalized_speaker_mode = "none"

        if prefill_audio is not None:
            prefill = np.asarray(prefill_audio).flatten()
            if prefill.dtype != np.int16:
                prefill = np.clip(prefill, -32768, 32767).astype(np.int16)
            for index in range(0, len(prefill), block_size):
                chunk = prefill[index : index + block_size]
                if len(chunk):
                    pre_roll.append(chunk.copy())
                    energy_samples.append(self._chunk_energy(chunk))

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=block_size,
        ) as stream:
            calibration_window = min(0.08 if fast_start else 0.8, start_timeout)
            while wait_seconds < calibration_window:
                chunk, _ = stream.read(block_size)
                audio = chunk.flatten()
                wait_seconds += len(audio) / self.sample_rate
                energy_samples.append(self._chunk_energy(audio))
                pre_roll.append(audio.copy())

            ambient = np.median(energy_samples) if energy_samples else 0.003
            start_threshold = max(ambient * 2.2, 0.010)
            end_threshold = max(ambient * 1.4, 0.006)

            while total_seconds < max_duration:
                chunk, _ = stream.read(block_size)
                audio = chunk.flatten()
                seconds = len(audio) / self.sample_rate
                energy = self._chunk_energy(audio)
                total_seconds += seconds

                if not speech_started:
                    pre_roll.append(audio.copy())
                    if energy >= start_threshold:
                        candidate_chunks = list(pre_roll)
                        candidate = np.concatenate(candidate_chunks).astype(np.int16) if candidate_chunks else audio.astype(np.int16)
                        similarity = self._speaker_similarity(candidate, speaker_reference)
                        speaker_ok = self._speaker_accepts(similarity, normalized_speaker_mode)
                        if not speaker_ok and normalized_speaker_mode == "light":
                            # In light mode we eventually allow non-matching voices to avoid hard rejection.
                            speaker_ok = wait_seconds >= max(0.8, start_timeout * 0.55)
                        if speaker_ok:
                            speech_started = True
                            captured.extend(candidate_chunks)
                            silence_seconds = 0.0
                            if callable(on_speech_start):
                                try:
                                    on_speech_start()
                                except Exception:
                                    pass
                        else:
                            wait_seconds += seconds
                            if wait_seconds >= start_timeout:
                                break
                    else:
                        wait_seconds += seconds
                        if wait_seconds >= start_timeout:
                            break
                    continue

                captured.append(audio.copy())
                if energy <= end_threshold:
                    silence_seconds += seconds
                else:
                    silence_seconds = 0.0

                spoken_seconds = sum(len(piece) for piece in captured) / self.sample_rate
                if spoken_seconds >= min_duration and silence_seconds >= silence_duration:
                    break

        if not captured:
            return None

        return np.concatenate(captured).astype(np.int16)

    def _speaker_similarity(self, audio, reference):
        if reference is None:
            return None
        try:
            current = _extract_features(np.asarray(audio, dtype=np.int16))
            return float(_cosine_similarity(reference, current))
        except Exception:
            return None

    def _speaker_accepts(self, similarity, mode):
        if mode == "none":
            return True
        if similarity is None:
            return mode == "light"
        if mode == "strong":
            return similarity >= self.strong_speaker_similarity
        return similarity >= self.light_speaker_similarity

    @staticmethod
    def _chunk_energy(audio):
        data = np.asarray(audio, dtype=np.float32) / 32768.0
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data))))

    @staticmethod
    def _normalize_transcript_text(text):
        value = unicodedata.normalize("NFKC", str(text or ""))
        replacements = (
            ("’", "'"),
            ("‘", "'"),
            ("`", "'"),
            ("“", '"'),
            ("”", '"'),
            ("–", "-"),
            ("—", "-"),
        )
        for source, target in replacements:
            value = value.replace(source, target)

        pattern_replacements = (
            (r"\bwi[\s\-]?fi\b", "wifi"),
            (r"\bwhy[\s\-]?fi\b", "wifi"),
            (r"\bblue[\s\-]?tooth\b", "bluetooth"),
            (r"\bblu[\s\-]?tooth\b", "bluetooth"),
            (r"\bair[\s\-]?plane\b", "airplane"),
            (r"\baeroplane\b", "airplane"),
            (r"\bflight mode\b", "airplane mode"),
            (r"\bnight[\s\-]?light\b", "night light"),
            (r"\bdot\s+pi\b", "dot py"),
            (r"\bc plus plus\b", "cpp"),
            (r"\bc plus\+\b", "cpp"),
        )
        for pattern, replacement in pattern_replacements:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

        value = re.sub(r"(\d+)\s*%", r"\1 percent", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @staticmethod
    def _is_english_text(text):
        if not text:
            return False
        total_letters = sum(1 for char in text if char.isalpha())
        if total_letters == 0:
            return False
        ascii_letters = sum(1 for char in text if char.isascii() and char.isalpha())
        return (ascii_letters / total_letters) >= 0.80

    def _is_usable_transcript(self, text):
        data = str(text or "").strip()
        if len(data) < self.min_transcript_chars:
            return False
        if len(re.findall(r"[A-Za-z0-9]", data)) < self.min_alnum_chars:
            return False
        return self._is_english_text(data)

    _JARGON_PATTERNS = (
        r"^(thank you|thanks for watching|subscribe|like and subscribe)$",
        r"^(music|applause|laughter|silence|\[.+\])$",
        r"^(okay|ok|um|uh|hmm|hm|ah)$",
        r"^\W+$",
    )
    _JARGON_RE = [re.compile(pattern, re.IGNORECASE) for pattern in _JARGON_PATTERNS]

    def _is_jargon(self, text):
        data = str(text or "").strip()
        return any(pattern.match(data) for pattern in self._JARGON_RE)

    def transcribe(self, audio, previous_text=""):
        if self.online and self.deepgram_api_key:
            try:
                text = self.deepgram_transcribe(audio)
                if text:
                    return text
            except Exception as exc:
                print(f"Deepgram STT failed ({exc}). Falling back to local Faster-Whisper.")

        return self.local_transcribe(audio, previous_text=previous_text, allow_download=False)

    def _audio_to_wav_bytes(self, audio):
        audio_np = np.asarray(audio).flatten()
        if audio_np.dtype != np.int16:
            audio_np = np.clip(audio_np, -32768, 32767).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_np.tobytes())
        return buffer.getvalue()

    def deepgram_transcribe(self, audio):
        if not self.deepgram_api_key:
            raise RuntimeError("Missing DEEPGRAM_API_KEY.")

        response = requests.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": self.deepgram_model,
                "language": self.deepgram_language,
                "smart_format": "true",
                "punctuate": "true",
            },
            headers={
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "audio/wav",
            },
            data=self._audio_to_wav_bytes(audio),
            timeout=self.deepgram_timeout_sec,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Deepgram HTTP {response.status_code}: {response.text}")

        data = response.json()
        transcript = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
        text = self._normalize_transcript_text(transcript)
        return text if self._is_usable_transcript(text) else None

    def local_transcribe(self, audio, previous_text="", allow_download=False):
        effective_allow_download = bool(allow_download) or self.whisper_allow_download
        if not self._ensure_local_model(allow_download=effective_allow_download):
            print(f"Local Faster-Whisper unavailable. Last load error: {self._last_local_model_error}")
            return None

        try:
            audio_np = np.asarray(audio)
            if audio_np.dtype == np.int16:
                audio_np = audio_np.astype(np.float32) / 32768.0
            else:
                audio_np = audio_np.astype(np.float32)

            segments, _ = self.model.transcribe(
                audio_np,
                beam_size=self.whisper_beam_size,
                temperature=0,
                condition_on_previous_text=True,
                initial_prompt=previous_text,
                vad_filter=True,
                language="en",
                without_timestamps=True,
            )
        except Exception as exc:
            print(f"Local Faster-Whisper runtime failed ({exc}).")
            return None

        text_parts = []
        avg_logprob = 0.0
        count = 0
        for segment in segments:
            text_parts.append(segment.text)
            avg_logprob += float(segment.avg_logprob)
            count += 1

        if count == 0:
            return None

        avg_logprob /= count
        text = self._normalize_transcript_text(" ".join(text_parts))
        if not self._is_usable_transcript(text):
            return None
        if self._is_jargon(text):
            return None

        if avg_logprob < self.min_avg_logprob:
            command_terms = {
                "wifi",
                "bluetooth",
                "airplane mode",
                "brightness",
                "shutdown",
                "restart",
                "sleep",
                "open",
                "close",
                "create",
                "delete",
                "play",
                "text mode",
                "voice authentication",
            }
            lowered = text.lower()
            if not any(term in lowered for term in command_terms):
                return None

        return text
