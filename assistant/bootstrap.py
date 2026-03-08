"""
First-time setup wizard for the desktop assistant.
"""

import time

from .config import VOICE_PRESETS
from .security import prompt_password_setup
from .utils import enable_autostart
from .voice_auth import enroll_voice
from .wake_word import WakeWordDetector


def _tts_prompt(tts, text, wait_seconds=0.5):
    print(f"\nAssistant: {text}")
    if tts is not None:
        tts.speak(text, replace=True, interrupt=True)
        tts.wait_until_done(timeout=wait_seconds)


def _extract_assistant_name(raw_name):
    value = str(raw_name or "").strip().lower()
    if not value:
        return ""

    patterns = [
        r"(?:my assistant name is|call yourself|your name is|call you)\s+([a-z][a-z0-9 _-]+)$",
        r"^([a-z][a-z0-9 _-]+)$",
    ]
    for pattern in patterns:
        match = __import__("re").search(pattern, value, flags=__import__("re").IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .!?")
            candidate = " ".join(candidate.split())
            if candidate:
                return candidate
    return value


def run_first_time_setup(config, voice_engine=None, tts=None):
    print("\n" + "=" * 50)
    print("  Welcome to your AI Assistant - First Time Setup")
    print("=" * 50)

    _tts_prompt(tts, "Welcome. Let's configure your assistant.")
    time.sleep(0.4)

    _tts_prompt(tts, "What would you like to call me?")
    assistant_name = ""
    if voice_engine is not None:
        audio = voice_engine.record_until_silence(max_duration=5.0, silence_duration=0.9, min_duration=0.8)
        if audio is not None:
            assistant_name = _extract_assistant_name(voice_engine.transcribe(audio))

    if not assistant_name:
        typed = input("Type your assistant name (default: friday): ").strip()
        assistant_name = _extract_assistant_name(typed) or "friday"

    wake_variants = sorted(WakeWordDetector.build_wake_variants(assistant_name))
    config.update(
        {
            "assistant_name": assistant_name,
            "wake_variants": wake_variants,
            "language": "en",
        }
    )
    _tts_prompt(tts, f"My name is set to {assistant_name}.")

    _tts_prompt(
        tts,
        "Choose a voice preset. I can preview each voice before you confirm it.",
    )
    while True:
        choice = input("Voice preset [jarvis]: ").strip().lower() or "jarvis"
        match = next((name for name in VOICE_PRESETS if name in choice), None)
        if not match:
            print("Unknown voice preset. Try again.")
            continue
        if tts is not None:
            tts.apply_voice_preset(match)
            preview_name = config.get("assistant_name", assistant_name).title()
            _tts_prompt(tts, f"Hello. I am {preview_name}. This is the {match} voice. Keep it?")
        keep = input("Keep this voice? (yes/no): ").strip().lower()
        if keep in {"y", "yes"}:
            config.set("voice_preset", match)
            break

    print("\n" + "-" * 50)
    print("Please enter your password to start the assistant.")
    _tts_prompt(tts, "Please use the keyboard to set your access password.")
    config.set("password_hash", prompt_password_setup())
    _tts_prompt(tts, "Password secured.")

    if voice_engine is not None:
        _tts_prompt(
            tts,
            "Next we will capture your voice authentication sample. This is used to verify you after the wake word.",
        )
        sample_path = enroll_voice(voice_engine, strict=True)
        if sample_path:
            threshold_text = input("Voice authentication strictness [80]: ").strip() or "80"
            try:
                threshold = max(0, min(100, int(threshold_text)))
            except ValueError:
                threshold = 80
            config.update(
                {
                    "voice_sample_path": sample_path,
                    "voice_auth_threshold": threshold,
                }
            )
            _tts_prompt(tts, f"Voice authentication is configured at {threshold} percent strictness.")
        else:
            config.update({"voice_sample_path": "", "voice_auth_threshold": 0})
            _tts_prompt(tts, "Voice authentication setup failed. It can be configured later.")
    else:
        config.update({"voice_sample_path": "", "voice_auth_threshold": 0})

    autostart_result = enable_autostart()
    print(autostart_result)
    config.set("auto_start", "enabled" in autostart_result.lower() or "already enabled" in autostart_result.lower())

    config.set("is_setup_complete", True)

    print("\n" + "=" * 50)
    print("  Setup complete. Your assistant is ready.")
    print("=" * 50 + "\n")
    _tts_prompt(tts, "Setup complete. I'm ready.")
