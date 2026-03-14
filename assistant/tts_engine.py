"""
Text-to-Speech engine with cinematic voice presets.

Primary:   Edge-TTS (online) – configurable preset voices (JARVIS, TARS, etc.)
Fallback1: Piper (offline neural TTS)
Fallback2: pyttsx3 (offline system TTS, preset-matched voice)

The active voice preset determines Edge-TTS voice/rate/pitch and the
pyttsx3 fallback voice.  Users can switch presets at runtime.
"""

import asyncio
import os
import queue
import re
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    from playsound import playsound
except ImportError:
    playsound = None

try:
    import wave
    from piper.voice import PiperVoice
except ImportError:
    PiperVoice = None

try:
    from TTS.api import TTS as CoquiTTS
except ImportError:
    CoquiTTS = None
try:
    from TTS.tts.configs.xtts_config import XttsConfig
except Exception:
    XttsConfig = None

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import torch
except ImportError:
    torch = None

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

from .openvoice_engine import OpenVoiceEngine


class TTSEngine:
    def __init__(self, online=True):
        _ = online

        self.queue = queue.Queue()
        self.running = True
        self.stop_event = threading.Event()
        self.speaking_event = threading.Event()
        self.coqui_lock = threading.Lock()

        self.low_latency_mode = self._parse_bool_env("TTS_LOW_LATENCY_MODE", True)
        self.interrupt_on_new_speech = self._parse_bool_env("TTS_INTERRUPT_ON_NEW_SPEECH", True)

        # Edge-TTS defaults (overridden by apply_voice_preset)
        self.edge_voice = os.getenv("EDGE_TTS_VOICE", "en-GB-RyanNeural").strip() or "en-GB-RyanNeural"
        self.edge_rate = os.getenv("EDGE_TTS_RATE", "-5%").strip() or "-5%"
        self.edge_pitch = os.getenv("EDGE_TTS_PITCH", "-2Hz").strip() or "-2Hz"
        self.edge_volume = os.getenv("EDGE_TTS_VOLUME", "+0%").strip() or "+0%"
        self.edge_timeout = self._parse_int_env("EDGE_TTS_TIMEOUT_SEC", 12)
        self.edge_chunk_chars = self._parse_int_env("EDGE_TTS_CHUNK_CHARS", 48)

        # Piper TTS settings (offline neural fallback)
        self.piper_model_path = os.getenv("PIPER_MODEL_PATH", "").strip() or None
        self.piper_voice = None
        self.piper_disabled = False
        self.piper_last_error = None

        # pyttsx3 offline fallback
        self.local_engine = None
        self._pyttsx3_rate = 170
        self._pyttsx3_voice_keyword = "david"  # default to deeper male voice

        # Active preset name (for display)
        self.active_preset = "jarvis"
        self.custom_voice_name = ""
        self.custom_voice_enabled = False
        self.custom_voice_mode = ""
        self.custom_voice_profile = None
        self.custom_voice_sample_path = ""
        self.openvoice_engine = None
        self.xtts_model_name = os.getenv("XTTS_MODEL_NAME", "tts_models/multilingual/multi-dataset/xtts_v2").strip()
        preferred_device = os.getenv("XTTS_DEVICE", "").strip().lower()
        if preferred_device in {"cuda", "cpu"}:
            self.xtts_device = preferred_device
        else:
            self.xtts_device = "cuda" if (torch is not None and torch.cuda.is_available()) else "cpu"
        self.xtts_chunk_chars = self._parse_int_env("XTTS_CHUNK_CHARS", 180)
        self._xtts_engine = None
        self._piper_voice_cache = {}

        self.worker = threading.Thread(target=self._process_queue, daemon=True)
        self.worker.start()

        edge_state = "available" if edge_tts is not None and playsound is not None else "unavailable"
        piper_state = "available" if PiperVoice is not None else "unavailable (use 'pip install piper-tts')"
        print(
            "TTS initialized "
            f"(primary=Edge-TTS[{edge_state}], fallback=Piper[{piper_state}], voice={self.edge_voice})"
        )

    # ------------------------------------------------------------------
    # Voice preset management
    # ------------------------------------------------------------------

    def apply_voice_preset(self, preset_name: str) -> str:
        """
        Apply a named voice preset.  Returns a status message.

        Accepted names: jarvis, tars, friday, edith, nova, aurora, atlas
        """
        from .config import VOICE_PRESETS

        key = (preset_name or "").strip().lower()
        preset = VOICE_PRESETS.get(key)
        if preset is None:
            available = ", ".join(sorted(VOICE_PRESETS.keys()))
            return f"Unknown voice preset '{preset_name}'. Available: {available}"

        self.clear_custom_voice()

        self.edge_voice = preset["edge_voice"]
        self.edge_rate = preset.get("edge_rate", "+0%")
        self.edge_pitch = preset.get("edge_pitch", "+0Hz")
        self.edge_volume = preset.get("edge_volume", "+0%")
        self._pyttsx3_rate = preset.get("pyttsx3_rate", 175)
        self._pyttsx3_voice_keyword = preset.get("pyttsx3_voice_keyword", "david")
        self.active_preset = key

        # Reset pyttsx3 so it picks up new voice on next use
        if self.local_engine is not None:
            try:
                self.local_engine.stop()
            except Exception:
                pass
            self.local_engine = None

        desc = preset.get("description", key)
        print(f"Voice preset applied: {desc} ({self.edge_voice})")
        return f"Voice changed to {desc}."

    def apply_custom_voice(self, sample_path: str, profile_name: str = "custom") -> str:
        if isinstance(sample_path, dict):
            return self.apply_custom_voice_profile(sample_path)
        profile = {
            "mode": "coqui",
            "name": profile_name or "custom",
            "sample_path": sample_path,
        }
        return self.apply_custom_voice_profile(profile)

    def apply_custom_voice_profile(self, profile: dict) -> str:
        if not isinstance(profile, dict):
            return "Custom voice profile is invalid."

        mode = str(profile.get("mode") or "coqui").strip().lower()
        if mode not in {"coqui", "openvoice", "auto"}:
            return "Custom voice profile mode is not supported."

        name = str(profile.get("name") or "custom").strip() or "custom"
        if mode == "auto":
            mode = "openvoice" if OpenVoiceEngine.is_available() else "coqui"

        if mode == "coqui":
            sample_path = str(profile.get("sample_path") or "").strip()
            if not sample_path:
                return "No custom voice sample path was provided."
            if not Path(sample_path).exists():
                return f"Custom voice sample was not found: {sample_path}"
            prepared_sample = self._prepare_custom_voice_sample(sample_path)
            self.custom_voice_sample_path = prepared_sample
            self.custom_voice_profile = {
                "mode": "coqui",
                "name": name,
                "sample_path": prepared_sample,
            }
            self.custom_voice_mode = "coqui"
        else:
            speaker_path = str(profile.get("speaker_path") or profile.get("sample_path") or "").strip()
            if not speaker_path:
                return "No OpenVoice speaker sample path was provided."
            if not Path(speaker_path).exists():
                return f"OpenVoice speaker sample was not found: {speaker_path}"
            model_path = str(profile.get("openvoice_model_path") or "").strip()
            if model_path and not Path(model_path).exists():
                return f"OpenVoice model was not found: {model_path}"
            self.custom_voice_profile = {
                "mode": "openvoice",
                "name": name,
                "speaker_path": speaker_path,
                "openvoice_model_path": model_path,
            }
            self.custom_voice_mode = "openvoice"

        self.custom_voice_name = name
        self.custom_voice_enabled = True
        self.active_preset = f"custom:{self.custom_voice_name}"

        # Reset local engine so fallback remains consistent if custom synthesis fails.
        if self.local_engine is not None:
            try:
                self.local_engine.stop()
            except Exception:
                pass
            self.local_engine = None

        try:
            if self.custom_voice_mode == "coqui":
                self._ensure_xtts_model()
            else:
                self._get_openvoice_engine(self.custom_voice_profile)
        except Exception as exc:
            self.clear_custom_voice()
            return f"Custom voice could not be loaded. {exc}"

        label = "Coqui XTTS" if self.custom_voice_mode == "coqui" else "OpenVoice"
        return f"Voice changed to custom {label} profile '{self.custom_voice_name}'."

    def _prepare_custom_voice_sample(self, sample_path: str) -> str:
        candidate = Path(str(sample_path)).resolve()
        if candidate.suffix.lower() == ".wav":
            return str(candidate)
        if sf is None:
            return self._convert_with_pydub(candidate)
        try:
            audio, sample_rate = sf.read(str(candidate), dtype="float32")
            cache_dir = Path.home() / ".assistant" / "custom_voices" / "_processed"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", candidate.stem).strip("_") or "voice"
            stamp = int(candidate.stat().st_mtime)
            normalized = cache_dir / f"{safe_name}_{stamp}.wav"
            sf.write(str(normalized), audio, int(sample_rate), subtype="PCM_16")
            return str(normalized.resolve())
        except Exception:
            return self._convert_with_pydub(candidate)

    def _convert_with_pydub(self, candidate: Path) -> str:
        if AudioSegment is None:
            return str(candidate)
        try:
            audio = AudioSegment.from_file(str(candidate))
            cache_dir = Path.home() / ".assistant" / "custom_voices" / "_processed"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", candidate.stem).strip("_") or "voice"
            stamp = int(candidate.stat().st_mtime)
            normalized = cache_dir / f"{safe_name}_{stamp}.wav"
            audio = audio.set_channels(1).set_frame_rate(22050)
            audio.export(str(normalized), format="wav")
            return str(normalized.resolve())
        except Exception:
            return str(candidate)

    def _get_openvoice_engine(self, profile: dict):
        if not profile:
            raise RuntimeError("OpenVoice profile is missing.")
        if self.openvoice_engine is not None and self.custom_voice_profile == profile:
            return self.openvoice_engine
        engine = OpenVoiceEngine(
            model_path=str(profile.get("openvoice_model_path") or "").strip() or None,
            device=os.getenv("OPENVOICE_DEVICE", "").strip().lower() or None,
        )
        self.openvoice_engine = engine
        return engine

    def clear_custom_voice(self):
        self.custom_voice_enabled = False
        self.custom_voice_name = ""
        self.custom_voice_profile = None
        self.custom_voice_mode = ""
        self.custom_voice_sample_path = ""
        self.openvoice_engine = None

    def check_custom_voice_ready(self):
        if not self.custom_voice_enabled or not self.custom_voice_profile:
            return False, "Custom voice is not enabled."
        if self.custom_voice_mode not in {"coqui", "openvoice"}:
            return False, "Custom voice mode is unsupported."
        try:
            if self.custom_voice_mode == "coqui":
                self._ensure_xtts_model()
            else:
                self._get_openvoice_engine(self.custom_voice_profile)
        except Exception as exc:
            return False, str(exc)
        return True, ""

    def get_current_preset_info(self) -> str:
        from .config import VOICE_PRESETS
        preset = VOICE_PRESETS.get(self.active_preset, {})
        return preset.get("description", self.active_preset)

    def preview_current_voice(self, custom_text=None):
        label = self.active_preset.replace("_", " ").title()
        text = custom_text or f"This is the {label} voice profile."
        self.speak(text, replace=True, interrupt=True)
        return text

    # ------------------------------------------------------------------
    # Env parsing helpers
    # ------------------------------------------------------------------

    def _parse_int_env(self, key, default):
        raw = os.getenv(key)
        if not raw:
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

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def _drain_pending_queue(self):
        while True:
            try:
                _ = self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break

    def _interrupt_playback(self):
        self.stop_event.set()
        try:
            sd.stop()
        except Exception:
            pass

    def speak(self, text, replace=False, interrupt=False):
        text = (text or "").strip()
        if not text:
            return

        do_replace = replace or self.low_latency_mode
        do_interrupt = interrupt or (do_replace and self.interrupt_on_new_speech)
        if do_interrupt:
            self._interrupt_playback()
        if do_replace:
            self._drain_pending_queue()

        self.queue.put(text)

    def wait_until_done(self, timeout=5.0):
        if timeout is None:
            self.queue.join()
            return True

        end_time = time.time() + timeout
        while time.time() < end_time:
            if self.queue.unfinished_tasks == 0 and not self.speaking_event.is_set():
                return True
            time.sleep(0.05)
        return self.queue.unfinished_tasks == 0 and not self.speaking_event.is_set()

    def is_speaking(self):
        return self.speaking_event.is_set() or self.queue.unfinished_tasks > 0

    # ------------------------------------------------------------------
    # Edge-TTS  (online, primary voice)
    # ------------------------------------------------------------------

    def _chunk_text(self, text):
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return []

        parts = re.split(r"(?<=[\.\!\?\;\:])\s+", clean)
        chunks = []
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidate = part if not current else f"{current} {part}"
            if len(candidate) <= self.edge_chunk_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        return chunks or [clean]

    async def _edge_save(self, text, path):
        kwargs = {
            "text": text,
            "voice": self.edge_voice,
            "rate": self.edge_rate,
            "pitch": self.edge_pitch,
            "volume": self.edge_volume,
        }
        try:
            communicate = edge_tts.Communicate(receive_timeout=self.edge_timeout, **kwargs)
        except TypeError:
            communicate = edge_tts.Communicate(**kwargs)
        await communicate.save(str(path))

    def _edge_speak(self, text):
        if edge_tts is None or playsound is None:
            raise RuntimeError("Edge-TTS dependencies are not installed.")

        chunks = self._chunk_text(text)
        for chunk in chunks:
            if self.stop_event.is_set():
                return
            fd, tmp_name = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                asyncio.run(self._edge_save(chunk, tmp_path))
                if self.stop_event.is_set():
                    return
                playsound(str(tmp_path), block=True)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Piper TTS  (offline neural TTS – fallback 1)
    # ------------------------------------------------------------------

    def _get_piper_voice(self, model_path: str):
        if self.piper_disabled:
            raise RuntimeError(
                f"Piper TTS disabled after failures. Last error: {self.piper_last_error}"
            )
        if PiperVoice is None:
            self.piper_disabled = True
            self.piper_last_error = "piper-tts package is not installed."
            raise RuntimeError(self.piper_last_error)

        resolved = str(Path(model_path).expanduser().resolve())
        cached = self._piper_voice_cache.get(resolved)
        if cached is not None:
            return cached

        with self.coqui_lock:
            cached = self._piper_voice_cache.get(resolved)
            if cached is not None:
                return cached
            voice = PiperVoice.load(resolved)
            self._piper_voice_cache[resolved] = voice
            print(f"Loaded Piper TTS model: {resolved}")
            return voice

    def _ensure_piper_model(self):
        if self.piper_voice is not None:
            return
        if not self.piper_model_path or not Path(self.piper_model_path).exists():
            raise RuntimeError("Piper model path is not set or missing.")
        self.piper_voice = self._get_piper_voice(self.piper_model_path)

    def _play_wav_file(self, file_path):
        if sf is not None:
            audio, sample_rate = sf.read(str(file_path), dtype="float32")
            if self.stop_event.is_set():
                return
            sd.play(audio, sample_rate)
            sd.wait()
            return
        with wave.open(str(file_path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sample_width == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 4:
            audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

        if channels > 1:
            audio = audio.reshape(-1, channels)

        if self.stop_event.is_set():
            return
        sd.play(audio, sample_rate)
        sd.wait()

    def _ensure_xtts_model(self):
        if self._xtts_engine is not None:
            return
        if CoquiTTS is None:
            raise RuntimeError("Coqui TTS is not installed. Install package 'TTS' to enable voice cloning.")
        with self.coqui_lock:
            if self._xtts_engine is not None:
                return
            if torch is not None and XttsConfig is not None:
                try:
                    torch.serialization.add_safe_globals([XttsConfig])
                except Exception:
                    pass
            if torch is not None and self.xtts_device == "cuda":
                try:
                    torch.backends.cuda.matmul.allow_tf32 = True
                except Exception:
                    pass
            self._xtts_engine = CoquiTTS(self.xtts_model_name).to(self.xtts_device)

    def _xtts_speak(self, text):
        if not self.custom_voice_enabled or self.custom_voice_mode != "coqui":
            raise RuntimeError("Custom voice mode is not enabled.")
        if not self.custom_voice_sample_path:
            raise RuntimeError("Custom voice sample path is missing.")
        self._ensure_xtts_model()
        chunks = self._chunk_text_limit(text, self.xtts_chunk_chars)
        for chunk in chunks:
            if self.stop_event.is_set():
                return
            fd, tmp_name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                self._xtts_engine.tts_to_file(
                    text=chunk,
                    speaker_wav=self.custom_voice_sample_path,
                    language="en",
                    file_path=str(tmp_path),
                )
                self._play_wav_file(tmp_path)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _openvoice_speak(self, text):
        if not self.custom_voice_enabled or self.custom_voice_mode != "openvoice":
            raise RuntimeError("Custom voice mode is not enabled.")
        profile = self.custom_voice_profile or {}
        speaker_path = str(profile.get("speaker_path") or "").strip()
        if not speaker_path:
            raise RuntimeError("OpenVoice speaker sample path is missing.")
        engine = self._get_openvoice_engine(profile)
        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            engine.synthesize(text, speaker_path, tmp_path)
            self._play_wav_file(tmp_path)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _chunk_text_limit(self, text, limit):
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return []
        limit = max(60, int(limit))
        parts = re.split(r"(?<=[\.\!\?\;\:])\s+", clean)
        chunks = []
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidate = part if not current else f"{current} {part}"
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        return chunks or [clean]

    def _piper_speak(self, text):
        self._ensure_piper_model()
        chunks = self._chunk_text(text)

        for chunk in chunks:
            if self.stop_event.is_set():
                return

            fd, tmp_name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                import wave
                with wave.open(str(tmp_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.piper_voice.config.sample_rate)
                    self.piper_voice.synthesize(chunk, wf)

                self._play_wav_file(tmp_path)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # _piper_synthesize_to_file and RVC pipeline removed (custom voice now uses Coqui/OpenVoice)

    # ------------------------------------------------------------------
    # pyttsx3  (offline system TTS – fallback 2, preset-matched voice)
    # ------------------------------------------------------------------

    def _init_local_engine(self):
        """Initialize pyttsx3 with a voice matching the active preset."""
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 is not installed.")

        engine = pyttsx3.init()
        engine.setProperty("rate", self._pyttsx3_rate)
        engine.setProperty("volume", 1.0)

        # Try to find a voice matching the preset keyword
        keyword = self._pyttsx3_voice_keyword.lower()
        voices = engine.getProperty("voices") or []
        matched = False
        for v in voices:
            if keyword in (v.name or "").lower() or keyword in (v.id or "").lower():
                engine.setProperty("voice", v.id)
                matched = True
                print(f"Offline TTS voice: {v.name}")
                break

        if not matched and voices:
            # Prefer male voices for JARVIS/TARS presets
            if keyword in ("david", "mark", "george"):
                for v in voices:
                    if "male" in (getattr(v, "gender", "") or "").lower():
                        engine.setProperty("voice", v.id)
                        break

        self.local_engine = engine

    def _local_speak(self, text):
        if self.local_engine is None:
            self._init_local_engine()
        self.local_engine.say(text)
        self.local_engine.runAndWait()

    # ------------------------------------------------------------------
    # Queue worker  (cascading fallback:  Edge → Piper → pyttsx3)
    # ------------------------------------------------------------------

    def _process_queue(self):
        while self.running:
            text = self.queue.get()
            if text is None:
                self.queue.task_done()
                break

            self.stop_event.clear()
            self.speaking_event.set()
            try:
                if self.custom_voice_enabled:
                    try:
                        if self.custom_voice_mode == "openvoice":
                            self._openvoice_speak(text)
                        else:
                            self._xtts_speak(text)
                        continue
                    except Exception as custom_error:
                        print(f"  [TTS] Custom voice failed ({custom_error}). Falling back to standard voice.")
                self._edge_speak(text)
            except Exception as edge_error:
                try:
                    self._piper_speak(text)
                except Exception as piper_error:
                    self.piper_last_error = str(piper_error)
                    self.piper_disabled = True
                    
                    # Condense the error message so it doesn't spam the terminal
                    p_err = str(piper_error).replace("\n", " ").strip()
                    if len(p_err) > 60:
                        p_err = p_err[:57] + "..."
                    print(f"  [TTS Fallback] Edge/Piper failed ({p_err}) -> Using Pyttsx3.")
                    
                    try:
                        self._local_speak(text)
                    except Exception as local_error:
                        print(f"  [TTS] All engines failed. Last error: {local_error}")
            finally:
                self.speaking_event.clear()
                self.queue.task_done()

    # ------------------------------------------------------------------
    # Preload / stop
    # ------------------------------------------------------------------

    def preload_model(self, block=True):
        if not block:
            t = threading.Thread(target=self._safe_preload_piper, daemon=True)
            t.start()
            return True
        return self._safe_preload_piper()

    def _safe_preload_piper(self):
        try:
            self._ensure_piper_model()
            return True
        except Exception:
            return False

    def stop(self):
        self.stop_event.set()
        self._drain_pending_queue()
        try:
            sd.stop()
        except Exception:
            pass
        if self.local_engine is not None:
            try:
                self.local_engine.stop()
            except Exception:
                pass
