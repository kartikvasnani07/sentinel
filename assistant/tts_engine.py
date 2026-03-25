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
import platform
import queue
import re
import shutil
import subprocess
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
    import soundfile as sf
except ImportError:
    sf = None


class TTSEngine:
    def __init__(self, online=True):
        _ = online

        self.queue = queue.Queue()
        self.running = True
        self.stop_event = threading.Event()
        self.speaking_event = threading.Event()
        self.model_lock = threading.Lock()
        self.is_linux = platform.system().lower().startswith("linux")

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
        self._piper_voice_cache = {}

        self.worker = threading.Thread(target=self._process_queue, daemon=True)
        self.worker.start()

        edge_state = "available" if edge_tts is not None and playsound is not None else "unavailable"
        piper_state = "available" if PiperVoice is not None else "unavailable (use 'pip install piper-tts')"
        linux_state = "available" if self.is_linux else "n/a"
        print(
            "TTS initialized "
            f"(primary=Edge-TTS[{edge_state}], fallback=Piper[{piper_state}], linux_cli={linux_state}, voice={self.edge_voice})"
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

        self.queue.put({"text": text, "prefer_local": False})

    def speak_fast(self, text, replace=False, interrupt=False):
        text = (text or "").strip()
        if not text:
            return

        do_replace = replace or self.low_latency_mode
        do_interrupt = interrupt or (do_replace and self.interrupt_on_new_speech)
        if do_interrupt:
            self._interrupt_playback()
        if do_replace:
            self._drain_pending_queue()

        self.queue.put({"text": text, "prefer_local": True})

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

        with self.model_lock:
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

    # _piper_synthesize_to_file removed (not needed for current pipeline)

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
    # Linux CLI TTS fallback (system speech tools)
    # ------------------------------------------------------------------

    def _linux_cli_speak(self, text):
        if not self.is_linux:
            raise RuntimeError("Linux CLI TTS is unavailable on this platform.")
        if shutil.which("spd-say"):
            subprocess.run(["spd-say", text], check=False)
            return
        if shutil.which("espeak-ng"):
            subprocess.run(["espeak-ng", text], check=False)
            return
        if shutil.which("espeak"):
            subprocess.run(["espeak", text], check=False)
            return
        if shutil.which("festival"):
            subprocess.run(["festival", "--tts"], input=text, text=True, check=False)
            return
        raise RuntimeError("No Linux TTS CLI (spd-say/espeak/festival) is available.")

    # ------------------------------------------------------------------
    # Queue worker  (cascading fallback:  Edge → Piper → pyttsx3)
    # ------------------------------------------------------------------

    def _process_queue(self):
        while self.running:
            item = self.queue.get()
            if item is None:
                self.queue.task_done()
                break

            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                prefer_local = bool(item.get("prefer_local"))
            else:
                text = str(item or "").strip()
                prefer_local = False
            if not text:
                self.queue.task_done()
                continue

            self.stop_event.clear()
            self.speaking_event.set()
            try:
                if prefer_local:
                    try:
                        if self.is_linux:
                            try:
                                self._linux_cli_speak(text)
                                return
                            except Exception:
                                pass
                        self._local_speak(text)
                    except Exception:
                        try:
                            self._piper_speak(text)
                        except Exception:
                            self._edge_speak(text)
                else:
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
                        if self.is_linux:
                            try:
                                self._linux_cli_speak(text)
                                return
                            except Exception:
                                pass
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
