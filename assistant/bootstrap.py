"""
First-time setup wizard for the desktop assistant.
"""

import re
import time

from .config import VOICE_PRESETS
from .security import prompt_password_setup
from .utils import disable_autostart
from .voice_auth import enroll_voice
from .wake_word import WakeWordDetector


YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "confirm", "proceed", "do it"}
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "dont", "do not", "negative"}


def _tts_prompt(tts, text, wait_seconds=3.0):
    print(f"\nAssistant: {text}")
    if tts is not None:
        tts.speak(text, replace=True, interrupt=True)
        tts.wait_until_done(timeout=wait_seconds)


def _normalize_text(text):
    return " ".join(str(text or "").strip().lower().replace("'", "").split())


def _interpret_yes_no(text):
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if normalized in YES_WORDS or any(item in normalized for item in YES_WORDS):
        return True
    if normalized in NO_WORDS or any(item in normalized for item in NO_WORDS):
        return False
    return None


def _ask_text_or_voice(prompt, *, voice_engine=None, tts=None, default=""):
    typed = input(prompt).strip()
    if typed:
        return typed
    if voice_engine is None:
        return default

    reminder = "You can say your answer now."
    print(f"Assistant: {reminder}")
    if tts is not None:
        tts.speak(reminder, replace=True, interrupt=True)
        tts.wait_until_done(timeout=2.4)
    audio = voice_engine.record_until_silence(
        max_duration=5.5,
        silence_duration=0.8,
        min_duration=0.3,
        start_timeout=2.5,
        fast_start=True,
    )
    if audio is None:
        return default
    transcript = (voice_engine.transcribe(audio) or "").strip()
    return transcript or default


def _confirm_text_or_voice(question, *, default=True, voice_engine=None, tts=None):
    for _ in range(2):
        answer = _ask_text_or_voice(f"{question} [yes/no]: ", voice_engine=voice_engine, tts=tts, default="")
        interpreted = _interpret_yes_no(answer)
        if interpreted is not None:
            return interpreted
        print("Please answer yes or no.")
        if tts is not None:
            tts.speak("Please answer yes or no.", replace=True, interrupt=True)
            tts.wait_until_done(timeout=2.6)
    return bool(default)


def _extract_assistant_name(raw_name):
    value = str(raw_name or "").strip().lower()
    if not value:
        return ""

    patterns = [
        r"(?:my assistant name is|call yourself|your name is|call you)\s+([a-z][a-z0-9 _-]+)$",
        r"^([a-z][a-z0-9 _-]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .!?")
            candidate = " ".join(candidate.split())
            if candidate:
                return candidate
    return value


def _print_voice_presets():
    print("\nAvailable voice presets:")
    for index, (name, preset) in enumerate(VOICE_PRESETS.items(), start=1):
        description = preset.get("description", name).strip()
        print(f"  {index}. {name} - {description}")


def _resolve_voice_choice(choice):
    cleaned = str(choice or "").strip().lower()
    ordered = list(VOICE_PRESETS.keys())
    if not cleaned:
        return "jarvis"
    if cleaned.isdigit():
        index = int(cleaned) - 1
        if 0 <= index < len(ordered):
            return ordered[index]
    if cleaned in VOICE_PRESETS:
        return cleaned
    return next((name for name in VOICE_PRESETS if name in cleaned), "")


def _resolve_interface_choice(choice):
    normalized = _normalize_text(choice)
    if not normalized:
        return True, "waves", "waves"
    if "bubble" in normalized:
        return True, "bubble", "bubble"
    if "text" in normalized or "textual" in normalized or "plain" in normalized:
        return False, "waves", "text"
    if "off" in normalized or "disable" in normalized:
        return False, "waves", "text"
    return True, "waves", "waves"


def run_first_time_setup(config, voice_engine=None, tts=None):
    print("\n" + "=" * 50)
    print("  Welcome to your AI Assistant - First Time Setup")
    print("=" * 50)

    _tts_prompt(tts, "Welcome. Let's configure your assistant.")
    time.sleep(0.3)

    while True:
        _tts_prompt(tts, "What would you like to call me? You can type it or say it.")
        raw_name = _ask_text_or_voice(
            "Assistant name (default: friday): ",
            voice_engine=voice_engine,
            tts=tts,
            default="friday",
        )
        assistant_name = _extract_assistant_name(raw_name) or "friday"
        if _confirm_text_or_voice(
            f"Use {assistant_name} as my name?",
            default=True,
            voice_engine=voice_engine,
            tts=tts,
        ):
            break

    wake_variants = sorted(WakeWordDetector.build_wake_variants(assistant_name))
    config.update(
        {
            "assistant_name": assistant_name,
            "wake_variants": wake_variants,
            "language": "en",
        }
    )
    _tts_prompt(tts, f"My name is set to {assistant_name}.")

    wake_response_enabled = _confirm_text_or_voice(
        "Enable wake-word response after summon?",
        default=True,
        voice_engine=voice_engine,
        tts=tts,
    )
    config.set("wake_response_enabled", wake_response_enabled)
    if wake_response_enabled:
        _tts_prompt(tts, "Wake response is enabled.")
    else:
        _tts_prompt(tts, "Wake response is disabled. I will listen immediately after wake word detection.")

    _tts_prompt(tts, "Choose your interface mode: waves, bubble, or text-only.")
    interface_choice = _ask_text_or_voice(
        "Interface mode [waves/bubble/text, default: waves]: ",
        voice_engine=voice_engine,
        tts=tts,
        default="waves",
    )
    visuals_enabled, ui_mode, label = _resolve_interface_choice(interface_choice)
    config.update({"waves_enabled": visuals_enabled, "ui_mode": ui_mode})
    if visuals_enabled:
        _tts_prompt(tts, f"{label.title()} interface selected.")
    else:
        _tts_prompt(tts, "Text-only interface selected.")

    _tts_prompt(
        tts,
        "Choose a voice preset. I can preview each voice before you confirm it.",
    )
    _print_voice_presets()
    while True:
        choice = _ask_text_or_voice(
            "Voice preset [jarvis] (name, number, or 'list'): ",
            voice_engine=voice_engine,
            tts=tts,
            default="jarvis",
        ).strip().lower()
        if choice in {"list", "show", "help", "?"}:
            _print_voice_presets()
            continue
        match = _resolve_voice_choice(choice)
        if not match:
            print("Unknown voice preset. Try again.")
            _print_voice_presets()
            continue
        if tts is not None:
            result = tts.apply_voice_preset(match)
            print(result)
            preview_name = config.get("assistant_name", assistant_name).title()
            preview_text = f"Hello. I am {preview_name}. This is the {match} voice profile."
            tts.preview_current_voice(preview_text)
            tts.wait_until_done(timeout=8.0)
            print(f"Previewed {match}.")
        if _confirm_text_or_voice(
            "Keep this voice profile?",
            default=True,
            voice_engine=voice_engine,
            tts=tts,
        ):
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
            threshold_text = _ask_text_or_voice(
                "Voice authentication strictness [80]: ",
                voice_engine=voice_engine,
                tts=tts,
                default="80",
            ).strip() or "80"
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

    autostart_result = disable_autostart()
    print(autostart_result)
    config.set("auto_start", False)

    config.set("is_setup_complete", True)

    print("\n" + "=" * 50)
    print("  Setup complete. Your assistant is ready.")
    print("=" * 50 + "\n")
    _tts_prompt(tts, "Setup complete. I'm ready.")
