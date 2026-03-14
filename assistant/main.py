import html
import os
import pathlib
import re
import sys
import threading
import time
import wave
from urllib.parse import quote_plus

import keyboard
import requests

try:
    import msvcrt
except ImportError:
    msvcrt = None

from .bootstrap import run_first_time_setup
from .config import AssistantConfig, UI_MODES, VOICE_PRESETS
from .fallback_manager import FallbackManager
from .intent_engine import IntentEngine
from .llm_engine import LLMEngine
from .memory import Memory
from .security import prompt_password_check, prompt_password_reset
from .streaming_pipeline import StreamingPipeline
from .system_actions import SystemActions
from .terminal_wave import TerminalWaveRenderer
from .tts_engine import TTSEngine
from .utils import disable_autostart, enable_autostart, random_greeting, random_wake_response
from .voice_auth import enroll_voice
from .voice_engine import VoiceEngine
from .wake_word import WakeWordDetector


INTERRUPT_STOP_WORDS = {"stop", "cancel", "quiet", "pause", "never mind", "wait"}
YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "correct", "do it", "go ahead", "proceed", "please do"}
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "dont", "do not", "negative", "never mind"}
TEXT_VOICE_ENABLE_PHRASES = {
    "enable voice mode",
    "turn on voice mode",
    "speak responses",
    "read responses aloud",
    "enable speaking",
}
TEXT_VOICE_DISABLE_PHRASES = {
    "disable voice mode",
    "turn off voice mode",
    "mute responses",
    "stop reading responses",
}
MEDIA_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".wmv",
    ".mpeg",
}
OPTION_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _normalize_text(text):
    return " ".join(str(text or "").lower().replace("'", "").split())


def _strip_html_tags(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _extract_response_options(text):
    data = str(text or "").strip()
    if not data:
        return []
    options = []
    seen = set()
    for line in data.splitlines():
        match = re.match(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.+)$", line.strip())
        if not match:
            continue
        candidate = match.group(1).strip(" .")
        if len(candidate) < 2 or len(candidate) > 180:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(candidate)
        if len(options) >= 10:
            break
    return options


def _resolve_selected_option(query, options):
    normalized = _normalize_text(query)
    if not normalized or not options:
        return ""

    numbered = re.search(r"\b(?:option|choice|select|pick)\s*(\d{1,2})\b", normalized)
    if numbered:
        index = int(numbered.group(1)) - 1
        if 0 <= index < len(options):
            return options[index]

    for word, value in OPTION_ORDINALS.items():
        if re.search(rf"\b{re.escape(word)}\b", normalized):
            index = value - 1
            if 0 <= index < len(options):
                return options[index]

    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(options):
            return options[index]

    for option in options:
        option_normalized = _normalize_text(option)
        if option_normalized and (
            normalized == option_normalized
            or normalized in option_normalized
            or option_normalized in normalized
        ):
            return option
    return ""


def _expand_query_with_previous_options(query, system):
    options = system.session_context.get("last_response_options")
    if not isinstance(options, list) or not options:
        return query
    selected = _resolve_selected_option(query, options)
    if not selected:
        return query
    system.session_context["last_selected_option"] = selected
    return selected


def _looks_like_information_query(query):
    normalized = _normalize_text(query)
    if not normalized:
        return False
    if str(query or "").strip().endswith("?"):
        return True
    info_tokens = {
        "what",
        "who",
        "when",
        "where",
        "why",
        "how",
        "explain",
        "meaning",
        "define",
        "tell",
        "difference",
        "guide",
    }
    return any(token in normalized.split() for token in info_tokens)


def _needs_web_fallback(response):
    lowered = _normalize_text(response)
    if not lowered:
        return True
    fallback_markers = {
        "i dont know",
        "i do not know",
        "not sure",
        "unable to answer",
        "no information available",
        "couldnt find",
        "could not find",
        "system action error",
        "unknown system action",
    }
    return any(marker in lowered for marker in fallback_markers)


def _fetch_web_snippets(query, limit=5):
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, timeout=8, headers=headers)
        if response.status_code != 200:
            return []
        body = response.text
    except Exception:
        return []

    snippets = []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>)',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(body):
        url_value = match.group(1).strip()
        title = _strip_html_tags(match.group(2))
        snippet_raw = match.group(3) or match.group(4) or ""
        snippet = _strip_html_tags(snippet_raw)
        if not title:
            continue
        snippets.append({"title": title, "snippet": snippet, "url": url_value})
        if len(snippets) >= limit:
            break
    return snippets


def _build_web_fallback_response(query, llm):
    snippets = _fetch_web_snippets(query, limit=5)
    if not snippets:
        return ""

    references = "\n".join(
        f"{index}. {item['title']} | {item['snippet']} | {item['url']}"
        for index, item in enumerate(snippets, start=1)
    )
    prompt = (
        "Answer the user's query using only the following web snippets.\n"
        "Respond in English, concise, and mention if confidence is limited.\n"
        f"User query: {query}\n\nWeb snippets:\n{references}\n\nAnswer:"
    )
    try:
        answer = str(llm.generate(prompt)).strip()
    except Exception:
        answer = ""

    if not answer:
        best = snippets[0]
        answer = f"{best['title']}: {best['snippet']}".strip()

    top_links = ", ".join(item["url"] for item in snippets[:2] if item.get("url"))
    if top_links:
        answer = f"{answer}\n\nSources: {top_links}"
    return answer


def _clear_terminal_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _prepare_terminal_for_text(wave_renderer=None):
    if wave_renderer is not None and getattr(wave_renderer, "enabled", False):
        wave_renderer.pause_and_clear()
    _clear_terminal_screen()


def _is_complex_terminal_response(text):
    data = str(text or "")
    if not data.strip():
        return False
    if len(data) >= 200:
        return True
    if "\n" in data:
        return True
    lowered = data.lower()
    return any(marker in lowered for marker in {"(yes/no)", "choose", "multiple", "enter "})


def _sanitize_assistant_name(raw_name):
    value = str(raw_name or "").strip().lower()
    patterns = [
        r"(?:my assistant name is|call yourself|your name is|call you|change your name to|set your name to)\s+([a-z][a-z0-9 _-]+)$",
        r"^([a-z][a-z0-9 _-]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            candidate = " ".join(match.group(1).strip(" .!?").split())
            if candidate:
                return candidate
    return " ".join(value.split())


def _record_terminal_response(terminal_state, system, response):
    text = str(response or "").strip()
    terminal_state["last_response"] = text
    system.session_context["last_terminal_response"] = text
    system.session_context["last_suggestion_text"] = text
    system.session_context["last_response_options"] = _extract_response_options(text)


def _resolve_local_setting_status(query, params, config):
    raw = _normalize_text(query or params.get("raw_text") or "")
    setting = _normalize_text(params.get("setting") or "")
    combined = f"{setting} {raw}".strip()
    if not combined:
        return None

    if any(token in combined for token in {"wake", "summon", "call"}) and any(
        token in combined for token in {"sensitivity", "sensitvity", "sensitive"}
    ):
        return f"Wake-word sensitivity is set to {int(config.get('wake_sensitivity', 65))} percent."

    if any(token in combined for token in {"wake", "summon", "call"}) and any(
        token in combined for token in {"response", "reply", "ack", "acknowledgement", "acknowledgment"}
    ):
        enabled = bool(config.get("wake_response_enabled", True))
        return f"Wake-word response is {'enabled' if enabled else 'disabled'}."

    if any(token in combined for token in {"wave", "waves", "ascii"}):
        enabled = bool(config.get("waves_enabled", True))
        mode = str(config.get("ui_mode", "waves") or "waves").strip().lower()
        if not enabled:
            return "Visual interface is disabled. Text-only interface is active."
        return f"{mode.title()} interface is enabled."

    if "bubble" in combined:
        enabled = bool(config.get("waves_enabled", True))
        mode = str(config.get("ui_mode", "waves") or "waves").strip().lower()
        return "Bubble interface is enabled." if enabled and mode == "bubble" else "Bubble interface is disabled."

    if "voice model" in combined or "voice preset" in combined or "voice selection" in combined:
        active_custom = str(config.get("active_custom_voice") or "").strip()
        if active_custom:
            profiles = _get_custom_voice_profiles(config)
            mode = next((item.get("mode") for item in profiles if item.get("name") == active_custom), "")
            label = "Custom"
            if mode == "openvoice":
                label = "OpenVoice custom"
            elif mode == "coqui":
                label = "Coqui custom"
            return f"Current voice model is {label} profile {active_custom}."
        preset = str(config.get("voice_preset", "jarvis")).strip() or "jarvis"
        return f"Current voice model is {preset}."

    if "voice authentication" in combined or "voice auth" in combined or "voice verification" in combined:
        threshold = int(config.get("voice_auth_threshold", 0))
        if threshold <= 0:
            return "Voice authentication is disabled."
        return f"Voice authentication is enabled at {threshold} percent strictness."

    if "humor" in combined:
        return f"Humor level is set to {int(config.get('humor_level', 50))} percent."

    if "language" in combined:
        return "English is the active language."

    return None


def _extract_media_request_from_text(text):
    lowered = str(text or "").lower()
    platform = "spotify" if "spotify" in lowered else "youtube" if ("youtube" in lowered or "video" in lowered) else ""
    creator_match = re.search(r"\b(?:from|by)\s+([a-z0-9_.-]+)", lowered)
    topic_match = re.search(r"\b(?:about|on)\s+([a-z0-9 ,._-]+)", lowered)
    if not platform and not creator_match and not topic_match:
        return None
    pieces = []
    if creator_match:
        pieces.append(creator_match.group(1).strip())
    if topic_match:
        pieces.append(topic_match.group(1).strip(" ."))
    query = " ".join(piece for piece in pieces if piece).strip()
    if not query and platform:
        query = "recommended result"
    return {"platform": platform or "youtube", "query": query}


def _format_history(memory):
    rows = memory.list_conversations()
    if not rows:
        return "No conversation history is available."
    lines = ["Conversation history:"]
    for row in rows:
        marker = " <- current" if row.get("is_current") else ""
        lines.append(
            f"{row.get('id')} | {row.get('title')} | messages: {row.get('message_count')} | updated: {row.get('updated_at')}{marker}"
        )
    lines.append("Use: open conversation <id> to continue a specific chat.")
    return "\n".join(lines)


def _open_new_conversation(memory, system, terminal_state):
    conversation_id = memory.start_new_conversation()
    system.clear_context()
    terminal_state["last_response"] = ""
    return f"Started new conversation {conversation_id}."


def _is_media_extension(path_like):
    suffix = pathlib.Path(str(path_like or "")).suffix.lower()
    return suffix in MEDIA_EXTENSIONS


def _pick_media_candidate(candidates):
    if not candidates:
        return None
    media_candidates = [item for item in candidates if item.is_file() and _is_media_extension(item.name)]
    if media_candidates:
        return media_candidates[0]
    file_candidates = [item for item in candidates if item.is_file()]
    if file_candidates:
        return file_candidates[0]
    return candidates[0]


def _is_local_media_request(query, params):
    normalized = _normalize_text(query)
    if any(token in normalized for token in {"youtube", "youtube music", "spotify"}):
        if not any(key in normalized for key in {"directory", "folder", "file", "current directory", "inside"}):
            if not params.get("application"):
                return False
    if params.get("directory") is not None:
        return True
    if params.get("application"):
        return True
    if any(token in normalized for token in {"directory", "folder", "file", "current directory", "inside", "documents", "downloads", "desktop"}):
        return True
    requested = str(params.get("song") or params.get("name") or "").strip()
    return _is_media_extension(requested)


def _resolve_media_play_request(system, params, query, *, tts, voice, is_text_mode):
    if not _is_local_media_request(query, params):
        return None, None, ""

    requested_name = str(params.get("song") or params.get("name") or "").strip()
    requested_name = re.sub(
        r"\b(?:in|inside|from)\s+(?:the\s+)?(?:current\s+(?:directory|folder)|[a-z0-9_ ./\\:-]+)\b.*$",
        "",
        requested_name,
        flags=re.IGNORECASE,
    ).strip(" .")
    if not requested_name:
        return None, None, "No local media file name was provided."

    specified_directory = params.get("directory")
    candidates_in_directory = []
    if specified_directory is not None:
        candidates_in_directory = system.find_path_candidates(
            requested_name,
            directory=specified_directory,
            source_hint="file",
        )
    global_candidates = system.find_path_candidates(requested_name, source_hint="file")

    selected = None
    if candidates_in_directory:
        selected = _pick_media_candidate(candidates_in_directory)
    elif global_candidates:
        selected = _pick_media_candidate(global_candidates)
        if specified_directory:
            prompt = (
                f"I could not find {requested_name} in {specified_directory}. "
                f"I found {selected}. Do you want to continue with that file?"
            )
            if not _confirm_prompt(prompt, tts=tts, voice=voice, is_text_mode=is_text_mode):
                return None, None, "Playback cancelled."
    else:
        return None, None, ""

    if selected is None:
        return None, None, ""

    if len(global_candidates) > 1 and not candidates_in_directory:
        selected_text = _prompt_choice(
            "Multiple close media files were found. Choose one to open:",
            [str(item) for item in global_candidates],
            tts=tts,
            voice=voice,
        )
        if selected_text:
            selected = next(
                (item for item in global_candidates if str(item) == selected_text or item.name == selected_text),
                selected,
            )

    routed = {
        "raw_text": query,
        "path": str(selected),
        "name": selected.name,
    }
    if params.get("application"):
        routed["application"] = params.get("application")
    return "open_path", routed, ""


def _apply_contextual_follow_up(action, params, system):
    generic_requests = {
        "that video",
        "the video",
        "this video",
        "that song",
        "the song",
        "this song",
        "that",
        "it",
        "play it",
        "open it",
        "run it",
        "play that",
        "open that",
        "run that",
    }
    if action == "play_music":
        song = _normalize_text(params.get("song") or params.get("name") or "")
        if song in generic_requests:
            media = system.session_context.get("last_media_request") or _extract_media_request_from_text(
                system.session_context.get("last_suggestion_text", "")
            )
            if media:
                params["song"] = media.get("query") or "recommended result"
                params["platform"] = media.get("platform") or params.get("platform")
    if action == "open_path":
        website = _normalize_text(params.get("website") or "")
        if website in {"that", "it", "that video", "that channel", "that site"}:
            last_website = system.session_context.get("last_website")
            if last_website:
                params["website"] = last_website
    return params


def _speak_with_barge_in(
    tts,
    text,
    *,
    wake_detector=None,
    voice=None,
    timeout=8.0,
    wave_renderer=None,
    hide_waves_during_speech=False,
):
    if not text:
        return ""
    has_live_voice_loop = wake_detector is not None and voice is not None
    restore_voice_threshold = None
    if has_live_voice_loop and wake_detector.get_voice_reference() is not None:
        restore_voice_threshold = wake_detector.voice_auth_threshold
        if restore_voice_threshold < 60:
            wake_detector.voice_auth_threshold = 60
    should_hide_waves = bool(hide_waves_during_speech or _is_complex_terminal_response(text))
    if wave_renderer is not None and has_live_voice_loop:
        if should_hide_waves:
            wave_renderer.clear_for_response()
            wave_renderer.set_mode("paused")
        else:
            wave_renderer.set_mode("speaking")
    tts.speak(text, replace=True, interrupt=True)
    if not has_live_voice_loop:
        tts.wait_until_done(timeout=timeout)
        return ""
    while tts.is_speaking():
        if wake_detector.listen_for_wake_word(
            timeout=1.1,
            on_audio_level=(wave_renderer.set_audio_level if (wave_renderer is not None and not should_hide_waves) else None),
        ):
            if wave_renderer is not None:
                wave_renderer.set_mode("recording")
            tts.stop()
            audio = voice.record_until_silence(
                max_duration=8.0,
                silence_duration=0.6,
                min_duration=0.2,
                start_timeout=1.8,
                fast_start=True,
                prefill_audio=wake_detector.consume_recent_audio(),
                speaker_reference=wake_detector.get_voice_reference(),
                speaker_mode=wake_detector.query_speaker_mode(),
                on_speech_start=tts.stop,
                on_audio_chunk=(wave_renderer.set_audio_level if wave_renderer is not None else None),
            )
            transcript = voice.transcribe(audio) if audio is not None else ""
            if wave_renderer is not None:
                wave_renderer.set_mode("processing")
            if restore_voice_threshold is not None:
                wake_detector.voice_auth_threshold = restore_voice_threshold
            return transcript or "__INTERRUPTED__"
    tts.wait_until_done(timeout=timeout)
    if wave_renderer is not None:
        wave_renderer.set_mode("idle")
        if should_hide_waves:
            wave_renderer.pulse(0.3)
    if restore_voice_threshold is not None:
        wake_detector.voice_auth_threshold = restore_voice_threshold
    return ""


def _capture_voice_text(voice, *, max_duration=4.0, silence_duration=0.6, min_duration=0.2, start_timeout=2.6):
    if voice is None:
        return ""
    audio = voice.record_until_silence(
        max_duration=max_duration,
        silence_duration=silence_duration,
        min_duration=min_duration,
        start_timeout=start_timeout,
        fast_start=True,
    )
    if audio is None:
        return ""
    return (voice.transcribe(audio) or "").strip()


def _prompt_yes_no(question, *, tts=None, voice=None, default=False):
    prompt = question.rstrip()
    if "press Enter to answer by voice" not in prompt.lower():
        prompt = f"{prompt} (press Enter to answer by voice): "
    answer = input(prompt).strip().lower()
    interpreted = _interpret_yes_no(answer)
    if interpreted is not None:
        return interpreted
    if voice is None:
        return bool(default)

    voice_prompt = "Please say yes or no."
    print(f"Assistant: {voice_prompt}")
    if tts is not None:
        tts.speak(voice_prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=3.0)
    transcript = _capture_voice_text(voice)
    interpreted = _interpret_yes_no(transcript)
    if interpreted is not None:
        return interpreted
    return bool(default)


def _prompt_text_or_voice(prompt, *, tts=None, voice=None, max_duration=5.0):
    typed = input(f"{prompt} (press Enter to answer by voice): ").strip()
    if typed:
        return typed
    if voice is None:
        return ""
    reminder = "You can say your answer now."
    print(f"Assistant: {reminder}")
    if tts is not None:
        tts.speak(reminder, replace=True, interrupt=True)
        tts.wait_until_done(timeout=3.0)
    return _capture_voice_text(voice, max_duration=max_duration, silence_duration=0.7, min_duration=0.3, start_timeout=3.0).strip()


def _interpret_yes_no(text):
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if normalized in YES_WORDS or any(phrase in normalized for phrase in YES_WORDS):
        return True
    if normalized in NO_WORDS or any(phrase in normalized for phrase in NO_WORDS):
        return False
    return None


def _confirm_prompt(prompt, *, tts, voice, is_text_mode):
    print(f"Assistant: {prompt}")
    if tts is not None:
        tts.speak(prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=6.0)
    return _prompt_yes_no(f"{prompt} (yes/no)", tts=tts, voice=voice, default=False)


def _prompt_choice(question, options, *, tts=None, voice=None):
    print(question)
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    answer = input("Enter the number or exact file name (press Enter to answer by voice): ").strip()
    if not answer and voice is not None:
        prompt = "Please say the number or exact name."
        print(f"Assistant: {prompt}")
        if tts is not None:
            tts.speak(prompt, replace=True, interrupt=True)
            tts.wait_until_done(timeout=4.0)
        answer = _normalize_text(_capture_voice_text(voice, max_duration=5.0, silence_duration=0.7, min_duration=0.3, start_timeout=3.0))
    if not answer:
        return ""
    if answer.isdigit():
        index = int(answer) - 1
        if 0 <= index < len(options):
            return options[index]
    return answer


def _get_custom_voice_profiles(config):
    profiles = config.get("custom_voice_profiles", [])
    if not isinstance(profiles, list):
        return []
    cleaned = []
    for item in profiles:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        mode = str(item.get("mode") or "coqui").strip().lower()
        if mode not in {"coqui", "openvoice", "auto"}:
            continue
        if mode == "coqui":
            sample_path = str(item.get("sample_path") or item.get("path") or "").strip()
            if not name or not sample_path:
                continue
            cleaned.append({"name": name, "mode": "coqui", "sample_path": sample_path})
            continue
        speaker_path = str(item.get("speaker_path") or item.get("sample_path") or "").strip()
        if not name or not speaker_path:
            continue
        profile = {"name": name, "mode": mode, "speaker_path": speaker_path}
        openvoice_model_path = str(item.get("openvoice_model_path") or "").strip()
        if openvoice_model_path:
            profile["openvoice_model_path"] = openvoice_model_path
        cleaned.append(profile)
    return cleaned[:10]


def _print_voice_preset_options(config=None):
    print("\nAvailable voice models:")
    for index, (name, preset) in enumerate(VOICE_PRESETS.items(), start=1):
        description = preset.get("description", name).strip()
        print(f"  {index}. {name} - {description}")
    if config is not None:
        custom_profiles = _get_custom_voice_profiles(config)
        if custom_profiles:
            print("\nSaved custom voices:")
            for index, profile in enumerate(custom_profiles, start=1):
                if profile.get("mode") == "openvoice":
                    speaker_name = pathlib.Path(profile.get("speaker_path", "")).name
                    label = f"OpenVoice speaker: {speaker_name}"
                else:
                    sample_name = pathlib.Path(profile.get("sample_path", "")).name
                    label = f"Coqui sample: {sample_name}"
                print(f"  c{index}. {profile['name']} - {label}")
    print("\nCommands: 'add custom' to add a custom voice, 'delete' to remove one, 'list' to show this menu.")


def _resolve_voice_preset_choice(choice):
    cleaned = str(choice or "").strip().lower()
    ordered = list(VOICE_PRESETS.keys())
    if not cleaned:
        return ""
    if cleaned.isdigit():
        index = int(cleaned) - 1
        if 0 <= index < len(ordered):
            return ordered[index]
    if cleaned in VOICE_PRESETS:
        return cleaned
    return next((name for name in VOICE_PRESETS if name in cleaned), "")


def _resolve_custom_voice_choice(choice, profiles):
    cleaned = str(choice or "").strip().lower()
    if not cleaned:
        return None
    if cleaned.startswith("c") and cleaned[1:].isdigit():
        index = int(cleaned[1:]) - 1
        if 0 <= index < len(profiles):
            return profiles[index]
    if cleaned.isdigit():
        index = int(cleaned) - 1
        if 0 <= index < len(profiles):
            return profiles[index]
    for profile in profiles:
        if str(profile.get("name") or "").strip().lower() == cleaned:
            return profile
    for profile in profiles:
        if cleaned in str(profile.get("name") or "").strip().lower():
            return profile
    return None


def _save_voice_audio_sample(audio, sample_rate, destination):
    destination = pathlib.Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(int(sample_rate))
        stream.writeframes(audio.astype("int16").tobytes())
    return str(destination)


def _record_custom_voice_sample(voice, destination, *, tts=None):
    if voice is None:
        return "", "Voice capture is unavailable."
    prompt = "Press and hold Enter to record your voice. Release Enter to stop."
    print(f"Assistant: {prompt}")
    if tts is not None:
        tts.speak(prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=4.0)
    audio = None
    if hasattr(voice, "record_while_key_pressed"):
        audio = voice.record_while_key_pressed("enter", max_duration=60.0, start_timeout=15.0)
    if audio is None:
        audio = voice.record_until_silence(
            max_duration=20.0,
            silence_duration=1.2,
            min_duration=2.0,
            start_timeout=5.0,
            fast_start=True,
        )
    if audio is None:
        return "", "No audio was captured for the custom voice sample."
    saved = _save_voice_audio_sample(audio, voice.sample_rate, destination)
    return saved, ""


def _register_custom_voice_profile(
    config,
    *,
    name,
    mode,
    sample_path="",
    speaker_path="",
    openvoice_model_path="",
):
    display_name = str(name or "").strip()
    if not display_name:
        return False, "Custom voice name is required."

    mode = str(mode or "coqui").strip().lower()
    if mode not in {"coqui", "openvoice", "auto"}:
        return False, "Unsupported custom voice mode."

    if mode == "coqui":
        path_text = str(sample_path or "").strip().strip('"').strip("'")
        if not path_text:
            return False, "Custom voice sample path is required."
        sample = pathlib.Path(path_text)
        if not sample.exists():
            return False, f"Voice sample was not found: {sample}"
        record = {"name": display_name, "mode": "coqui", "sample_path": str(sample.resolve())}
    else:
        path_text = str(speaker_path or "").strip().strip('"').strip("'")
        if not path_text:
            return False, "OpenVoice speaker sample path is required."
        speaker = pathlib.Path(path_text)
        if not speaker.exists():
            return False, f"OpenVoice speaker sample was not found: {speaker}"
        record = {
            "name": display_name,
            "mode": mode,
            "speaker_path": str(speaker.resolve()),
        }
        model_text = str(openvoice_model_path or "").strip().strip('"').strip("'")
        if model_text:
            model_path = pathlib.Path(model_text)
            if not model_path.exists():
                return False, f"OpenVoice model was not found: {model_path}"
            record["openvoice_model_path"] = str(model_path.resolve())

    profiles = _get_custom_voice_profiles(config)
    existing = next((item for item in profiles if item["name"].lower() == display_name.lower()), None)
    if existing is None and len(profiles) >= 10:
        return False, "You can save up to 10 custom voice profiles. Delete one before adding another."

    if existing is not None:
        profiles = [record if item["name"].lower() == display_name.lower() else item for item in profiles]
    else:
        profiles.append(record)
    config.update({"custom_voice_profiles": profiles})
    return True, f"Saved custom voice profile '{display_name}'."


def _delete_custom_voice_profile(config, *, name_or_index):
    profiles = _get_custom_voice_profiles(config)
    if not profiles:
        return False, "No custom voice profiles are saved."
    token = str(name_or_index or "").strip().lower()
    if not token:
        return False, "No custom voice name or number was provided."

    target = None
    if token.startswith("c") and token[1:].isdigit():
        index = int(token[1:]) - 1
        if 0 <= index < len(profiles):
            target = profiles[index]
    elif token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(profiles):
            target = profiles[index]
    else:
        target = next((item for item in profiles if item["name"].lower() == token), None)
        if target is None:
            target = next((item for item in profiles if token in item["name"].lower()), None)

    if target is None:
        return False, "No matching custom voice profile was found."

    updated = [item for item in profiles if item["name"].lower() != target["name"].lower()]
    config.update({"custom_voice_profiles": updated})
    if str(config.get("active_custom_voice") or "").strip().lower() == target["name"].lower():
        config.set("active_custom_voice", "")
    return True, f"Deleted custom voice profile '{target['name']}'."


def _setup_custom_voice_profile_interactively(config, tts, *, voice=None):
    mode_choice = _prompt_text_or_voice(
        "Custom voice engine (coqui/openvoice/auto)",
        tts=tts,
        voice=voice,
        max_duration=4.0,
    ).strip().lower()
    if not mode_choice:
        mode_choice = "coqui"
    if mode_choice in {"cancel", "stop", "exit"}:
        return False, "Custom voice setup cancelled."
    if mode_choice not in {"coqui", "openvoice", "auto"}:
        mode_choice = "coqui"
    resolved_mode = mode_choice
    if mode_choice == "auto":
        resolved_mode = "openvoice" if os.getenv("OPENVOICE_INFER_CMD") else "coqui"

    source = input(
        "Custom voice source [path/record/cancel] (Enter for voice input): "
    ).strip().lower()
    if not source and voice is not None:
        source_prompt = "Say path or record."
        print(f"Assistant: {source_prompt}")
        tts.speak(source_prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=3.0)
        source = _normalize_text(
            _capture_voice_text(
                voice, max_duration=4.0, silence_duration=0.7, min_duration=0.2, start_timeout=2.8
            )
        )
    if source in {"cancel", "stop", "exit"}:
        return False, "Custom voice setup cancelled."

    sample_path = ""
    if source in {"record", "live", "mic", "microphone"}:
        custom_dir = pathlib.Path.home() / ".assistant" / "custom_voices"
        timestamp = int(time.time())
        destination = custom_dir / f"sample_{timestamp}.wav"
        sample_path, error = _record_custom_voice_sample(voice, destination, tts=tts)
        if error:
            return False, error
    else:
        if source not in {"path", ""}:
            sample_path = source
        if not sample_path:
            sample_path = _prompt_text_or_voice(
                "Enter path to voice sample file",
                tts=tts,
                voice=voice,
                max_duration=6.0,
            ).strip().strip('"').strip("'")
        if not sample_path:
            return False, "No custom voice sample path was provided."

    openvoice_model_path = ""
    if resolved_mode == "openvoice":
        openvoice_model_path = _prompt_text_or_voice(
            "OpenVoice model path (optional, blank to skip)",
            tts=tts,
            voice=voice,
            max_duration=5.0,
        ).strip().strip('"').strip("'")
        if _normalize_text(openvoice_model_path) in {"skip", "none", "no"}:
            openvoice_model_path = ""

    profile_name = _prompt_text_or_voice(
        "Save this custom voice as name",
        tts=tts,
        voice=voice,
        max_duration=4.5,
    ).strip()
    if not profile_name:
        return False, "Custom voice name was not provided."

    saved, message = _register_custom_voice_profile(
        config,
        name=profile_name,
        mode=resolved_mode,
        sample_path=sample_path if resolved_mode == "coqui" else "",
        speaker_path=sample_path if resolved_mode != "coqui" else "",
        openvoice_model_path=openvoice_model_path,
    )
    if not saved:
        return False, message

    profile = next(
        (item for item in _get_custom_voice_profiles(config) if item["name"].lower() == profile_name.lower()),
        None,
    )
    if not profile:
        return False, "Custom voice profile was saved but could not be loaded."

    result = tts.apply_custom_voice_profile(profile)
    print(result)
    ok, err = tts.check_custom_voice_ready() if hasattr(tts, "check_custom_voice_ready") else (True, "")
    if not ok:
        tts.clear_custom_voice()
        config.set("active_custom_voice", "")
        return False, f"Custom voice could not be loaded. {err}"
    preview_line = f"This is custom voice {profile['name']}. Do you want to use this voice?"
    if not _confirm_prompt(preview_line, tts=tts, voice=voice, is_text_mode=False):
        return False, "Custom voice not applied."

    config.set("active_custom_voice", profile["name"])
    return True, f"Custom voice applied: {profile['name']}."


def _apply_active_voice_from_config(config, tts):
    active_custom = str(config.get("active_custom_voice") or "").strip()
    custom_profiles = _get_custom_voice_profiles(config)
    if active_custom:
        profile = next((item for item in custom_profiles if item["name"] == active_custom), None)
        if profile:
            applied = tts.apply_custom_voice_profile(profile)
            if "could not" not in applied.lower() and "not found" not in applied.lower():
                return applied
    tts.clear_custom_voice()
    return tts.apply_voice_preset(config.get("voice_preset", "jarvis"))


def _setup_voice_model_interactively(config, tts, *, voice=None):
    _print_voice_preset_options(config)
    while True:
        choice = input("Voice model (name/number, 'list', blank to use voice/cancel): ").strip().lower()
        if not choice and voice is not None:
            prompt = "Please say the voice model name."
            print(f"Assistant: {prompt}")
            tts.speak(prompt, replace=True, interrupt=True)
            tts.wait_until_done(timeout=4.0)
            choice = _normalize_text(_capture_voice_text(voice, max_duration=5.0, silence_duration=0.7, min_duration=0.3, start_timeout=2.8))
        if not choice:
            return False, "Voice model setup cancelled."
        if choice in {"list", "show", "help", "?"}:
            _print_voice_preset_options(config)
            continue
        if choice in {"add custom", "custom", "custom voice", "record custom voice"}:
            applied, result = _setup_custom_voice_profile_interactively(config, tts, voice=voice)
            if applied:
                return True, result
            print(result)
            continue
        if choice in {"delete custom", "remove custom", "delete voice", "remove voice", "delete", "remove"}:
            delete_target = _prompt_text_or_voice(
                "Delete custom voice by name or number",
                tts=tts,
                voice=voice,
                max_duration=4.5,
            )
            delete_target = _normalize_text(delete_target)
            ok, message = _delete_custom_voice_profile(config, name_or_index=delete_target)
            print(message)
            if ok:
                continue
            continue
        custom_choice = _resolve_custom_voice_choice(choice, _get_custom_voice_profiles(config))
        if custom_choice is not None:
            result = tts.apply_custom_voice_profile(custom_choice)
            print(result)
            preview_line = f"This is custom voice {custom_choice['name']}. Do you want to use this voice?"
            if _confirm_prompt(preview_line, tts=tts, voice=voice, is_text_mode=False):
                config.set("active_custom_voice", custom_choice["name"])
                return True, f"Custom voice applied: {custom_choice['name']}."
            print("Voice model not applied. Choose another model.")
            continue
        preset = _resolve_voice_preset_choice(choice)
        if not preset:
            print("Unknown voice model. Please choose from the list.")
            continue
        result = tts.apply_voice_preset(preset)
        print(result)
        if "Unknown voice preset" in result:
            continue
        preview_line = f"This is the {preset} voice model. Do you want to use this voice?"
        print(f"Assistant: {preview_line}")
        tts.speak(preview_line, replace=True, interrupt=True)
        tts.wait_until_done(timeout=8.0)
        if _confirm_prompt("Use this voice model?", tts=tts, voice=voice, is_text_mode=False):
            config.set("voice_preset", preset)
            config.set("active_custom_voice", "")
            _preview_active_voice(config, tts)
            return True, f"Voice model changed to {preset}."
        print("Voice model not applied. Choose another model.")


def _resolve_transfer_request(system, action, params, *, tts, voice, is_text_mode):
    resolved = dict(params)
    source_hint = "directory" if action == "change_directory" else None

    name = str(resolved.get("name") or "").strip()
    source_dir = resolved.get("source_dir") or resolved.get("directory")
    if name and not resolved.get("path"):
        candidates = system.find_path_candidates(name, directory=source_dir, source_hint=source_hint)
        if not candidates:
            return None, "No matching source file or folder was found."
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            selected = _prompt_choice(
                "Multiple source matches found. Choose the exact source path:",
                [str(item) for item in candidates],
                tts=tts,
                voice=voice,
            )
            if not selected:
                return None, "Transfer cancelled."
            chosen = next((item for item in candidates if str(item) == selected or item.name == selected), None)
            if chosen is None:
                return None, "Transfer cancelled."
        resolved["path"] = str(chosen)
        resolved["name"] = chosen.name

    if action in {"copy_path", "move_path", "duplicate_path"}:
        destination = str(resolved.get("destination") or "").strip()
        if destination:
            destination_candidates = system.find_path_candidates(destination, source_hint="directory")
            if len(destination_candidates) == 1:
                resolved["destination"] = str(destination_candidates[0])
            elif len(destination_candidates) > 1:
                selected = _prompt_choice(
                    "Multiple destination directories matched. Choose the destination path:",
                    [str(item) for item in destination_candidates],
                    tts=tts,
                    voice=voice,
                )
                if not selected:
                    return None, "Transfer cancelled."
                chosen = next((item for item in destination_candidates if str(item) == selected or item.name == selected), None)
                if chosen is None:
                    return None, "Transfer cancelled."
                resolved["destination"] = str(chosen)
            else:
                override = input(
                    f"Destination path '{destination}' was not found. Enter a full destination path (blank to keep current): "
                ).strip()
                if override:
                    resolved["destination"] = override

    return resolved, ""


def _resolve_delete_request(system, params, *, tts, voice, is_text_mode):
    candidates = system.find_path_candidates(params.get("name", ""), directory=params.get("directory"), source_hint=None)
    if not candidates:
        return None, "No matching file or folder was found to delete."

    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        choices = [str(candidate) for candidate in candidates]
        selected = _prompt_choice(
            "Multiple matching paths were found. Choose the exact one to delete:",
            choices,
            tts=tts,
            voice=voice,
        )
        if not selected:
            return None, "Deletion cancelled."
        chosen = next((candidate for candidate in candidates if str(candidate) == selected or candidate.name == selected), None)
        if chosen is None:
            return None, "Deletion cancelled."

    if not _confirm_prompt(f"Delete {chosen}?", tts=tts, voice=voice, is_text_mode=is_text_mode):
        return None, f"Deletion cancelled for {chosen.name}."

    resolved = dict(params)
    resolved["path"] = str(chosen)
    resolved["name"] = chosen.name
    return resolved, ""


def _resolve_existing_path_request(system, params, *, tts, voice, is_text_mode, source_hint=None, purpose="open"):
    requested_name = str(params.get("name") or "").strip()
    candidates = system.find_path_candidates(requested_name, directory=params.get("directory"), source_hint=source_hint)
    if not candidates and requested_name and params.get("directory") is not None:
        fallback_candidates = system.find_path_candidates(requested_name, directory=None, source_hint=source_hint)
        if fallback_candidates:
            selected = fallback_candidates[0]
            if len(fallback_candidates) > 1:
                picked = _prompt_choice(
                    "I found this file outside the specified directory. Choose the file to continue:",
                    [str(item) for item in fallback_candidates],
                    tts=tts,
                    voice=voice,
                )
                if picked:
                    selected = next(
                        (item for item in fallback_candidates if str(item) == picked or item.name == picked),
                        selected,
                    )
            if _confirm_prompt(
                f"I found {selected}, but not in {params.get('directory')}. Do you want to continue?",
                tts=tts,
                voice=voice,
                is_text_mode=is_text_mode,
            ):
                candidates = [selected]
    if not candidates:
        return None, f"No matching file or folder was found to {purpose}."

    target_lower = requested_name.lower()
    target_stem = pathlib.Path(requested_name).stem.lower()
    exact_matches = [
        candidate
        for candidate in candidates
        if candidate.name.lower() == target_lower or candidate.stem.lower() == target_stem
    ]

    chosen = exact_matches[0] if exact_matches else candidates[0]
    if len(candidates) > 1 and not exact_matches:
        choices = [str(candidate) for candidate in candidates]
        selected = _prompt_choice(
            f"Multiple close matches were found. Choose the exact path to {purpose}:",
            choices,
            tts=tts,
            voice=voice,
        )
        if not selected:
            return None, f"{purpose.title()} cancelled."
        chosen = next((candidate for candidate in candidates if str(candidate) == selected or candidate.name == selected), None)
        if chosen is None:
            return None, f"{purpose.title()} cancelled."
    elif not exact_matches:
        if not _confirm_prompt(
            f"I found {chosen}. Do you want me to {purpose} it?",
            tts=tts,
            voice=voice,
            is_text_mode=is_text_mode,
        ):
            return None, f"{purpose.title()} cancelled."

    resolved = dict(params)
    resolved["path"] = str(chosen)
    resolved["name"] = chosen.name
    return resolved, ""


def _resolve_create_request(system, params, *, tts=None, voice=None):
    requested_name = str(params.get("name") or "notes.txt").strip()
    directory = params.get("directory")
    if not directory:
        selected_path = _prompt_text_or_voice(
            f"Enter a directory path for '{requested_name}' (blank for Home)",
            tts=tts,
            voice=voice,
            max_duration=6.0,
        )
        if _normalize_text(selected_path) == "cancel":
            return None, "Creation cancelled."
        params["directory"] = selected_path or str(pathlib.Path.home())
        directory = params["directory"]
    candidates = system.find_path_candidates(requested_name, directory=directory, source_hint="file")
    if candidates:
        choices = [candidate.name for candidate in candidates]
        print("Similar files already exist:")
        for option in choices:
            print(f"  - {option}")
        replacement = input(
            f"Enter the exact file name to create instead of '{requested_name}' (blank to keep it, 'cancel' to stop): "
        ).strip()
        if _normalize_text(replacement) == "cancel":
            return None, "Creation cancelled."
        if replacement:
            params["name"] = replacement

    target_name = params.get("name", requested_name)
    if not _prompt_yes_no(f"Create {target_name}? (yes/no): ", tts=tts, voice=voice, default=False):
        return None, f"Creation cancelled for {target_name}."
    return params, ""


def _prompt_for_name(tts, voice, is_text_mode):
    typed = input("Enter the new assistant name (or press Enter to speak it): ").strip()
    if typed:
        return _sanitize_assistant_name(typed)
    if voice is not None:
        prompt = "What should I call myself?"
        print(f"Assistant: {prompt}")
        tts.speak(prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=3.0)
        transcript = _capture_voice_text(voice, max_duration=5.0, silence_duration=0.8, min_duration=0.3, start_timeout=2.8)
        name = _sanitize_assistant_name(transcript)
        if name:
            return name
    return _sanitize_assistant_name(typed)


def _apply_name_change(config, wake_detector, name):
    assistant_name = _sanitize_assistant_name(name)
    if not assistant_name:
        return "No valid assistant name was provided."
    variants = sorted(WakeWordDetector.build_wake_variants(assistant_name))
    config.update({"assistant_name": assistant_name, "wake_variants": variants})
    wake_detector.update_wake_word(assistant_name, variants)
    return f"My name is now {assistant_name}."


def _preview_active_voice(config, tts):
    assistant_name = config.get("assistant_name", "assistant").title()
    preview = f"Hello. I am {assistant_name}. This is the {tts.active_preset.title()} voice profile."
    tts.preview_current_voice(preview)
    tts.wait_until_done(timeout=6.0)
    return preview


def _run_setup_flow(
    config,
    voice,
    tts,
    llm,
    system,
    memory,
    terminal_state,
    wake_detector,
    *,
    factory_reset=False,
    wave_renderer=None,
):
    if wave_renderer is not None:
        wave_renderer.set_enabled(False)
    _prepare_terminal_for_text(wave_renderer=None)

    if factory_reset:
        config.reset_factory_state(delete_voice_sample=True)
    run_first_time_setup(config, voice_engine=voice, tts=tts)
    llm.language = "en"
    llm.humor_level = config.get("humor_level", 50)
    _apply_active_voice_from_config(config, tts)
    system.clear_context()
    memory.clear()
    terminal_state["last_response"] = ""
    wake_detector.update_wake_word(config.get("assistant_name", "friday"), config.get("wake_variants", []))
    wake_detector.set_voice_reference(
        config.get("voice_sample_path", ""),
        config.get("voice_auth_threshold", 0),
    )
    wake_detector.set_sensitivity(config.get("wake_sensitivity", 65))

    if wave_renderer is not None:
        wave_renderer.set_style(config.get("ui_mode", "waves"))
        wave_renderer.set_enabled(bool(config.get("waves_enabled", True)))
        if wave_renderer.enabled:
            wave_renderer.set_mode("idle")

    return "Factory reset complete. Setup restarted." if factory_reset else "Setup complete."


def _maybe_process_follow_up(follow_up, **kwargs):
    if not follow_up or follow_up == "__INTERRUPTED__":
        return None
    if _normalize_text(follow_up) in INTERRUPT_STOP_WORDS:
        return None
    return _process_command(follow_up, **kwargs)


def _process_command(
    query,
    *,
    config,
    intent_engine,
    system,
    llm,
    tts,
    pipeline,
    voice,
    memory,
    wake_detector,
    terminal_state,
    text_mode_state,
    is_text_mode=False,
    wave_renderer=None,
):
    query = _expand_query_with_previous_options(query, system)
    context = dict(system.session_context)
    intent_data = intent_engine.detect(query, context=context)
    intent = intent_data.get("intent", "conversation")
    action = intent_data.get("action")
    params = intent_data.get("parameters", {})
    can_speak = (not is_text_mode) or bool(text_mode_state.get("voice_enabled"))

    def _show_terminal_output(message, force=False):
        text = str(message or "")
        show = (
            bool(force)
            or is_text_mode
            or _is_complex_terminal_response(text)
            or (wave_renderer is not None and not wave_renderer.enabled)
        )
        if show:
            if wave_renderer is not None and not is_text_mode and wave_renderer.enabled:
                _prepare_terminal_for_text(wave_renderer=wave_renderer)
            elif wave_renderer is not None and not is_text_mode and _is_complex_terminal_response(text):
                wave_renderer.clear_for_response()
                wave_renderer.set_mode("responding")
            print(text)
        return show

    if action == "enter_text_mode":
        return "ENTER_TEXT_MODE" if not is_text_mode else None

    if action == "new_conversation":
        result = _open_new_conversation(memory, system, terminal_state)
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "list_history":
        result = _format_history(memory)
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                "History listed on the terminal.",
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "open_conversation":
        if str(params.get("target") or "").strip().lower() == "last":
            conversation_id = memory.open_last_conversation()
            if conversation_id:
                system.clear_context()
                result = f"Opened conversation {conversation_id}."
            else:
                result = "No previous conversation was found."
        else:
            conversation_id = params.get("conversation_id") or input("Enter conversation id: ").strip()
            ok, result = memory.switch_to_conversation(conversation_id)
            if ok:
                system.clear_context()
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "delete_conversation":
        conversation_id = params.get("conversation_id") or input("Enter conversation id to delete: ").strip()
        normalized = str(conversation_id or "").strip()
        if not normalized:
            result = "No conversation id was provided."
            _show_terminal_output(result)
            _record_terminal_response(terminal_state, system, result)
            return None
        if not _confirm_prompt(f"Delete conversation {normalized}?", tts=tts, voice=voice, is_text_mode=is_text_mode):
            result = "Conversation deletion cancelled."
            _show_terminal_output(result)
            _record_terminal_response(terminal_state, system, result)
            return None
        ok, result = memory.delete_conversation(normalized)
        if ok:
            system.clear_context()
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "stop_assistant":
        result = "Shutting down the assistant."
        _show_terminal_output(result, force=True)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            tts.speak(result, replace=True, interrupt=True)
            tts.wait_until_done(timeout=4.0)
        return "EXIT_ASSISTANT"

    if action == "restart_setup":
        result = _run_setup_flow(
            config,
            voice,
            tts,
            llm,
            system,
            memory,
            terminal_state,
            wake_detector,
            factory_reset=False,
            wave_renderer=wave_renderer,
        )
        _show_terminal_output(result, force=True)
        _record_terminal_response(terminal_state, system, result)
        _preview_active_voice(config, tts)
        return None

    if action == "change_assistant_name":
        new_name = params.get("assistant_name") or _prompt_for_name(tts, voice, is_text_mode)
        result = _apply_name_change(config, wake_detector, new_name)
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        follow_up = ""
        if can_speak:
            follow_up = _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return _maybe_process_follow_up(
            follow_up,
            config=config,
            intent_engine=intent_engine,
            system=system,
            llm=llm,
            tts=tts,
            pipeline=pipeline,
            voice=voice,
            memory=memory,
            wake_detector=wake_detector,
            terminal_state=terminal_state,
            text_mode_state=text_mode_state,
            is_text_mode=is_text_mode,
            wave_renderer=wave_renderer,
        )

    if action == "clear_history":
        active_id = memory.current_conversation_id()
        memory.clear()
        system.clear_context()
        terminal_state["last_response"] = ""
        result = f"Cleared conversation {active_id}."
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "read_terminal":
        text = terminal_state.get("last_response") or "There is nothing on the terminal to read."
        _show_terminal_output(text, force=True)
        _record_terminal_response(terminal_state, system, text)
        _speak_with_barge_in(
            tts,
            text,
            wake_detector=wake_detector if not is_text_mode else None,
            voice=voice if not is_text_mode else None,
            timeout=15.0,
            wave_renderer=wave_renderer,
        )
        return None

    if action == "reset_password":
        if prompt_password_reset(config):
            result = "Password has been reset."
            _show_terminal_output(result)
            _record_terminal_response(terminal_state, system, result)
            if can_speak:
                _speak_with_barge_in(
                    tts,
                    result,
                    wake_detector=wake_detector if not is_text_mode else None,
                    voice=voice if not is_text_mode else None,
                    wave_renderer=wave_renderer,
                )
        return None

    if action == "set_voice_auth":
        threshold = params.get("threshold")
        if threshold is not None:
            config.set("voice_auth_threshold", threshold)
            result = "Voice authentication disabled." if threshold == 0 else f"Voice authentication set to {threshold} percent."
        else:
            sample_path = enroll_voice(voice)
            if sample_path:
                config.update({"voice_sample_path": sample_path, "voice_auth_threshold": 50})
                result = "Voice fingerprint updated."
            else:
                result = "Voice authentication setup failed."
        wake_detector.set_voice_reference(
            config.get("voice_sample_path", ""),
            config.get("voice_auth_threshold", 0),
        )
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_humor":
        level = params.get("level", 50)
        config.set("humor_level", level)
        llm.humor_level = level
        result = f"Humor level set to {level} percent."
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "change_language":
        requested = str(params.get("language") or "english").strip().lower()
        result = "English is active." if requested in {"", "en", "english", "default"} else "English is currently the only supported language in this build."
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "change_voice":
        restore_wave = bool(wave_renderer is not None and wave_renderer.enabled)
        if wave_renderer is not None and wave_renderer.enabled:
            wave_renderer.pause_and_clear()
            _clear_terminal_screen()
        preset = str(params.get("preset") or "").strip().lower()
        if not preset:
            applied, result = _setup_voice_model_interactively(config, tts, voice=voice)
        else:
            custom_profile = _resolve_custom_voice_choice(preset, _get_custom_voice_profiles(config))
            if custom_profile is not None:
                result = tts.apply_custom_voice_profile(custom_profile)
                ok, err = tts.check_custom_voice_ready() if hasattr(tts, "check_custom_voice_ready") else (True, "")
                if not ok:
                    tts.clear_custom_voice()
                    config.set("active_custom_voice", "")
                    result = f"Custom voice could not be loaded. {err}"
                elif "could not" not in result.lower() and "not found" not in result.lower():
                    config.set("active_custom_voice", custom_profile["name"])
            else:
                result = tts.apply_voice_preset(preset)
                if "Unknown voice preset" not in result:
                    config.set("voice_preset", preset)
                    config.set("active_custom_voice", "")
                    _preview_active_voice(config, tts)
        if wave_renderer is not None and restore_wave:
            wave_renderer.set_style(config.get("ui_mode", "waves"))
            wave_renderer.set_enabled(bool(config.get("waves_enabled", True)))
            if wave_renderer.enabled:
                wave_renderer.set_mode("idle")
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_autostart":
        result = enable_autostart() if params.get("on", True) else disable_autostart()
        config.set("auto_start", bool(params.get("on", True)))
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_wake_response":
        enabled = bool(params.get("on", True))
        config.set("wake_response_enabled", enabled)
        result = "Wake-word response enabled." if enabled else "Wake-word response disabled."
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_wave_display":
        enabled = bool(params.get("on", True))
        if enabled:
            config.update({"waves_enabled": True, "ui_mode": "waves"})
        else:
            config.set("waves_enabled", False)
        if wave_renderer is not None:
            if enabled:
                wave_renderer.set_style("waves")
                wave_renderer.set_enabled(True)
                wave_renderer.set_mode("idle")
                wave_renderer.pulse(0.4)
            else:
                wave_renderer.set_enabled(False)
                _prepare_terminal_for_text(wave_renderer=None)
        result = "ASCII waves enabled." if enabled else "ASCII waves disabled. Using text status interface."
        _show_terminal_output(result, force=True)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_bubble_display":
        enabled = bool(params.get("on", True))
        if enabled:
            config.update({"waves_enabled": True, "ui_mode": "bubble"})
        else:
            config.set("waves_enabled", False)
        if wave_renderer is not None:
            if enabled:
                wave_renderer.set_style("bubble")
                wave_renderer.set_enabled(True)
                wave_renderer.set_mode("idle")
                wave_renderer.pulse(0.5)
            else:
                wave_renderer.set_enabled(False)
                _prepare_terminal_for_text(wave_renderer=None)
        result = "Bubble interface enabled." if enabled else "Bubble interface disabled. Using text status interface."
        _show_terminal_output(result, force=True)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_interface_style":
        enabled = bool(params.get("on", True))
        style = str(params.get("style") or config.get("ui_mode", "waves")).strip().lower()
        if style not in UI_MODES:
            style = "waves"
        if enabled:
            config.update({"waves_enabled": True, "ui_mode": style})
        else:
            config.set("waves_enabled", False)
        if wave_renderer is not None:
            if enabled:
                wave_renderer.pause_and_clear()
                wave_renderer.set_style(style)
                wave_renderer.set_enabled(True)
                wave_renderer.set_mode("idle")
                wave_renderer.pulse(0.5)
            else:
                wave_renderer.set_enabled(False)
                _prepare_terminal_for_text(wave_renderer=None)
        result = (
            f"{style.title()} interface enabled."
            if enabled
            else f"{style.title()} interface disabled. Using text status interface."
        )
        _show_terminal_output(result, force=True)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if action == "set_wake_sensitivity":
        configured = params.get("percent")
        if configured is None:
            base = config.get("wake_sensitivity", 65)
            delta = int(params.get("delta") or 0)
            configured = max(0, min(100, int(base) + delta))
        config.set("wake_sensitivity", configured)
        wake_detector.set_sensitivity(configured)
        result = f"Wake-word sensitivity set to {configured} percent."
        _show_terminal_output(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    if intent == "system_command" and action:
        params.setdefault("raw_text", query)
        params = _apply_contextual_follow_up(action, params, system)

        if action == "play_music":
            routed_action, routed_params, routed_error = _resolve_media_play_request(
                system,
                params,
                query,
                tts=tts,
                voice=voice,
                is_text_mode=is_text_mode,
            )
            if routed_error:
                _show_terminal_output(routed_error, force=True)
                _record_terminal_response(terminal_state, system, routed_error)
                if can_speak:
                    _speak_with_barge_in(
                        tts,
                        routed_error,
                        wake_detector=wake_detector if not is_text_mode else None,
                        voice=voice if not is_text_mode else None,
                        wave_renderer=wave_renderer,
                    )
                return None
            if routed_action and routed_params:
                action = routed_action
                params = routed_params

        if action == "project_code":
            instruction = str(params.get("instruction") or "").strip()
            if not instruction:
                target = system._path_from_fragment(params.get("project_path"))
                if target is not None and target.is_file():
                    purpose = input("Describe what this file should do: ").strip()
                    if not purpose:
                        result = "Project edit cancelled because no purpose was provided."
                        _show_terminal_output(result, force=True)
                        _record_terminal_response(terminal_state, system, result)
                        return None
                    if not _confirm_prompt(
                        f"Apply corrections to {target.name} for this purpose?",
                        tts=tts,
                        voice=voice,
                        is_text_mode=is_text_mode,
                    ):
                        result = "Project edit cancelled."
                        _show_terminal_output(result, force=True)
                        _record_terminal_response(terminal_state, system, result)
                        return None
                    params["instruction"] = (
                        f"Fix this file so it correctly serves the purpose: {purpose}. "
                        "If needed, rewrite incorrect sections and preserve valid parts."
                    )
                elif target is not None and target.is_dir():
                    follow_instruction = input(
                        "Describe the project changes to apply (blank to cancel): "
                    ).strip()
                    if not follow_instruction:
                        result = "Project edit cancelled."
                        _show_terminal_output(result, force=True)
                        _record_terminal_response(terminal_state, system, result)
                        return None
                    params["instruction"] = follow_instruction

        if action in {"open_application", "close_application", "run_as_admin"} or (action == "open_path" and params.get("application")):
            app_match = system.resolve_application_request(params)
            if app_match.get("status") == "missing":
                result = f"I could not find an installed application matching {app_match.get('requested') or params.get('application') or 'that request'}."
                _show_terminal_output(result, force=True)
                _record_terminal_response(terminal_state, system, result)
                if can_speak:
                    _speak_with_barge_in(
                        tts,
                        result,
                        wake_detector=wake_detector if not is_text_mode else None,
                        voice=voice if not is_text_mode else None,
                        wave_renderer=wave_renderer,
                    )
                return None
            if app_match.get("status") == "needs_confirmation":
                prompt = (
                    f"I could not find {app_match.get('requested')} exactly. "
                    f"Do you want me to open {app_match.get('display_name')} instead?"
                )
                if not _confirm_prompt(prompt, tts=tts, voice=voice, is_text_mode=is_text_mode):
                    result = "Application action cancelled."
                    _show_terminal_output(result, force=True)
                    _record_terminal_response(terminal_state, system, result)
                    return None
            params["resolved_application"] = app_match

        if action == "create_file":
            resolved, error = _resolve_create_request(system, dict(params), tts=tts, voice=voice)
            if not resolved:
                _show_terminal_output(error, force=True)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if action in {"open_path", "read_file", "modify_file"} and params.get("name") and not params.get("website"):
            resolved, error = _resolve_existing_path_request(
                system,
                dict(params),
                tts=tts,
                voice=voice,
                is_text_mode=is_text_mode,
                source_hint="file" if action in {"read_file", "modify_file"} else None,
                purpose="read" if action == "read_file" else ("update" if action == "modify_file" else "open"),
            )
            if not resolved:
                _show_terminal_output(error, force=True)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if action in {"copy_path", "move_path", "rename_path", "duplicate_path"}:
            resolved, error = _resolve_transfer_request(system, action, dict(params), tts=tts, voice=voice, is_text_mode=is_text_mode)
            if not resolved:
                _show_terminal_output(error, force=True)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if action == "delete_path":
            resolved, error = _resolve_delete_request(system, dict(params), tts=tts, voice=voice, is_text_mode=is_text_mode)
            if not resolved:
                _show_terminal_output(error, force=True)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if wave_renderer is not None:
            wave_renderer.set_mode("processing")
        local_status = _resolve_local_setting_status(query, params, config) if action == "get_setting_status" else None
        result = local_status if local_status is not None else system.execute(action, params)
        if local_status is None and _needs_web_fallback(result) and _looks_like_information_query(query):
            web_result = _build_web_fallback_response(query, llm)
            if web_result:
                result = web_result
        _show_terminal_output(result)
        memory.add("user", query)
        memory.add("assistant", result)
        _record_terminal_response(terminal_state, system, result)
        if action == "play_music":
            system.session_context["last_media_request"] = {
                "platform": params.get("platform", "youtube"),
                "query": params.get("song") or params.get("name") or "",
            }
        if action == "open_path" and params.get("website"):
            system.session_context["last_website"] = params["website"]
        if can_speak:
            _speak_with_barge_in(
                tts,
                system.build_voice_summary(action, params, result),
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
                wave_renderer=wave_renderer,
            )
        return None

    memory.add("user", query)
    prompt = f"Conversation so far:\n{memory.get_context()}\n\nUser: {query}"
    if wave_renderer is not None:
        wave_renderer.set_mode("processing")
    response = pipeline.run(prompt, speak=False, display=is_text_mode)
    if _needs_web_fallback(response) and _looks_like_information_query(query):
        web_response = _build_web_fallback_response(query, llm)
        if web_response:
            response = web_response
    if wave_renderer is not None:
        if not is_text_mode:
            wave_renderer.clear_for_response()
            _show_terminal_output(response, force=True)
        wave_renderer.set_mode("responding")
    memory.add("assistant", response)
    _record_terminal_response(terminal_state, system, response)
    media = _extract_media_request_from_text(response)
    if media:
        system.session_context["last_media_request"] = media
    if can_speak:
        follow_up = _speak_with_barge_in(
            tts,
            response,
            wake_detector=wake_detector if not is_text_mode else None,
            voice=voice if not is_text_mode else None,
            timeout=12.0,
            wave_renderer=wave_renderer,
            hide_waves_during_speech=not is_text_mode,
        )
        return _maybe_process_follow_up(
            follow_up,
            config=config,
            intent_engine=intent_engine,
            system=system,
            llm=llm,
            tts=tts,
            pipeline=pipeline,
            voice=voice,
            memory=memory,
            wake_detector=wake_detector,
            terminal_state=terminal_state,
            text_mode_state=text_mode_state,
            is_text_mode=is_text_mode,
            wave_renderer=wave_renderer,
        )
    if wave_renderer is not None:
        wave_renderer.set_mode("idle")
    return None


def _run_text_mode(
    config,
    intent_engine,
    system,
    llm,
    tts,
    pipeline,
    memory,
    wake_detector,
    terminal_state,
    text_mode_state,
    text_mode_toggle_event,
    factory_reset_event,
    new_conversation_event,
    voice,
    wave_renderer=None,
):
    _clear_terminal_screen()
    print("\n" + "=" * 60)
    print("  TEXT MODE - type commands directly. Ctrl+Space exits text mode.")
    print("  Press Ctrl+Shift+Alt to start a new conversation.")
    print("  Use 'enable voice mode' if you want spoken responses here.")
    print("=" * 60 + "\n")

    if msvcrt is None:
        while True:
            if factory_reset_event.is_set():
                factory_reset_event.clear()
                return "FACTORY_RESET"
            if new_conversation_event.is_set():
                new_conversation_event.clear()
                result = _open_new_conversation(memory, system, terminal_state)
                print(result)
                _record_terminal_response(terminal_state, system, result)
            if text_mode_toggle_event.is_set():
                text_mode_toggle_event.clear()
                tts.stop()
                return None
            query = input("Text > ").strip()
            if not query:
                continue
            normalized = _normalize_text(query)
            if normalized in {"exit", "quit", "leave", "back"}:
                return None
            if normalized in TEXT_VOICE_ENABLE_PHRASES:
                text_mode_state["voice_enabled"] = True
                response = "Text mode voice responses enabled."
                print(response)
                _record_terminal_response(terminal_state, system, response)
                _speak_with_barge_in(tts, response, timeout=6.0, wave_renderer=wave_renderer)
                continue
            if normalized in TEXT_VOICE_DISABLE_PHRASES:
                text_mode_state["voice_enabled"] = False
                tts.stop()
                response = "Text mode voice responses disabled."
                print(response)
                _record_terminal_response(terminal_state, system, response)
                continue
            result = _process_command(
                query,
                config=config,
                intent_engine=intent_engine,
                system=system,
                llm=llm,
                tts=tts,
                pipeline=pipeline,
                voice=voice,
                memory=memory,
                wake_detector=wake_detector,
                terminal_state=terminal_state,
                text_mode_state=text_mode_state,
                is_text_mode=True,
                wave_renderer=wave_renderer,
            )
            if result == "EXIT_ASSISTANT":
                return "EXIT_ASSISTANT"
        return None

    buffer = []
    print("Text > ", end="", flush=True)
    while True:
        if factory_reset_event.is_set():
            factory_reset_event.clear()
            tts.stop()
            print()
            return "FACTORY_RESET"
        if new_conversation_event.is_set():
            new_conversation_event.clear()
            tts.stop()
            result = _open_new_conversation(memory, system, terminal_state)
            print(f"\n{result}\n")
            _record_terminal_response(terminal_state, system, result)
            print("Text > ", end="", flush=True)
            continue
        if text_mode_toggle_event.is_set():
            text_mode_toggle_event.clear()
            tts.stop()
            print("\nLeaving text mode.\n")
            return None
        if not msvcrt.kbhit():
            time.sleep(0.03)
            continue

        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            print()
            if tts.queue.unfinished_tasks > 0:
                tts.stop()
            query = "".join(buffer).strip()
            buffer.clear()
            if not query:
                print("Text > ", end="", flush=True)
                continue
            normalized = _normalize_text(query)
            if normalized in {"exit", "quit", "leave", "back"}:
                print("Leaving text mode.\n")
                return None
            if normalized in TEXT_VOICE_ENABLE_PHRASES:
                text_mode_state["voice_enabled"] = True
                response = "Text mode voice responses enabled."
                print(response)
                _record_terminal_response(terminal_state, system, response)
                _speak_with_barge_in(tts, response, timeout=6.0, wave_renderer=wave_renderer)
                print("\nText > ", end="", flush=True)
                continue
            if normalized in TEXT_VOICE_DISABLE_PHRASES:
                text_mode_state["voice_enabled"] = False
                tts.stop()
                response = "Text mode voice responses disabled."
                print(response)
                _record_terminal_response(terminal_state, system, response)
                print("\nText > ", end="", flush=True)
                continue

            result = _process_command(
                query,
                config=config,
                intent_engine=intent_engine,
                system=system,
                llm=llm,
                tts=tts,
                pipeline=pipeline,
                voice=voice,
                memory=memory,
                wake_detector=wake_detector,
                terminal_state=terminal_state,
                text_mode_state=text_mode_state,
                is_text_mode=True,
                wave_renderer=wave_renderer,
            )
            if result == "EXIT_ASSISTANT":
                print("Stopping assistant.\n")
                return "EXIT_ASSISTANT"
            print("\nText > ", end="", flush=True)
            continue

        if char == "\x08":
            if buffer:
                buffer.pop()
                print("\b \b", end="", flush=True)
            continue
        if char == "\x03":
            raise KeyboardInterrupt
        if char in {"\x00", "\xe0"}:
            _ = msvcrt.getwch()
            continue
        buffer.append(char)
        print(char, end="", flush=True)


def run():
    print("Starting Assistant...\n")
    config = AssistantConfig()
    first_time_setup = config.is_first_time_setup()

    if (not first_time_setup) and config.get("password_hash"):
        if not prompt_password_check(config.get("password_hash"), purpose="start the assistant"):
            print("Authentication failed. Exiting.")
            sys.exit(1)

    fallback = FallbackManager()
    voice = VoiceEngine(online=fallback.online)
    tts = TTSEngine(online=fallback.online)
    llm = LLMEngine(online=fallback.online)
    llm.language = "en"
    llm.humor_level = config.get("humor_level", 50)

    system = SystemActions(llm=llm)
    memory = Memory(max_turns=12)
    memory.start_new_conversation()
    terminal_state = {"last_response": ""}
    text_mode_state = {"voice_enabled": False}

    if first_time_setup:
        run_first_time_setup(config, voice_engine=voice, tts=tts)

    if first_time_setup and config.get("password_hash"):
        if not prompt_password_check(config.get("password_hash"), purpose="start the assistant"):
            print("Authentication failed. Exiting.")
            sys.exit(1)

    voice.warmup_local_model_async()

    _apply_active_voice_from_config(config, tts)
    intent_engine = IntentEngine(llm)
    pipeline = StreamingPipeline(llm, tts)

    keyboard.add_hotkey("shift+enter", pipeline.stop)
    keyboard.add_hotkey("shift+enter", tts.stop)

    base_dir = pathlib.Path(__file__).resolve().parent.parent
    model_path = base_dir / "models" / "vosk" / "vosk-model-small-en-us-0.15"
    wake_detector = WakeWordDetector(
        model_path=str(model_path),
        wake_word=config.get("assistant_name", "friday"),
        wake_variants=config.get("wake_variants", []),
        sensitivity=config.get("wake_sensitivity", 65),
    )
    wake_detector.set_voice_reference(
        config.get("voice_sample_path", ""),
        config.get("voice_auth_threshold", 0),
    )
    wake_detector.set_sensitivity(config.get("wake_sensitivity", 65))
    if sorted(config.get("wake_variants", [])) != sorted(wake_detector.wake_variants):
        config.set("wake_variants", sorted(wake_detector.wake_variants))

    text_mode_toggle_event = threading.Event()
    factory_reset_event = threading.Event()
    new_conversation_event = threading.Event()
    keyboard.add_hotkey("ctrl+space", text_mode_toggle_event.set)
    keyboard.add_hotkey("ctrl+shift+r", factory_reset_event.set)
    keyboard.add_hotkey("ctrl+shift+alt", new_conversation_event.set)
    wave_renderer = TerminalWaveRenderer(
        enabled=bool(config.get("waves_enabled", True)),
        fps=20,
        style=config.get("ui_mode", "waves"),
    )
    wave_renderer.start()
    if wave_renderer.enabled:
        wave_renderer.set_style(config.get("ui_mode", "waves"))
        wave_renderer.set_mode("idle")

    greeting = random_greeting()
    if wave_renderer.enabled:
        _prepare_terminal_for_text(wave_renderer=wave_renderer)
    print(f"\nAssistant: {greeting}\n")
    _record_terminal_response(terminal_state, system, greeting)
    _speak_with_barge_in(tts, greeting, wake_detector=wake_detector, voice=voice, wave_renderer=wave_renderer)

    try:
        while True:
            if factory_reset_event.is_set():
                factory_reset_event.clear()
                tts.stop()
                wave_renderer.set_mode("processing")
                result = _run_setup_flow(
                    config,
                    voice,
                    tts,
                    llm,
                    system,
                    memory,
                    terminal_state,
                    wake_detector,
                    factory_reset=True,
                    wave_renderer=wave_renderer,
                )
                if wave_renderer.enabled:
                    _prepare_terminal_for_text(wave_renderer=wave_renderer)
                print(result)
                _record_terminal_response(terminal_state, system, result)
                wave_renderer.set_mode("idle")
                continue

            if new_conversation_event.is_set():
                new_conversation_event.clear()
                result = _open_new_conversation(memory, system, terminal_state)
                print(result)
                _record_terminal_response(terminal_state, system, result)
                continue

            if text_mode_toggle_event.is_set():
                text_mode_toggle_event.clear()
                if wave_renderer is not None:
                    wave_renderer.set_enabled(False)
                _clear_terminal_screen()
                if config.get("password_hash") and not prompt_password_check(config.get("password_hash"), purpose="enter text mode"):
                    print("Access denied for text mode.")
                    if wave_renderer is not None:
                        wave_renderer.set_enabled(bool(config.get("waves_enabled", True)))
                        wave_renderer.set_style(config.get("ui_mode", "waves"))
                        if wave_renderer.enabled:
                            wave_renderer.set_mode("idle")
                    continue
                _clear_terminal_screen()
                text_mode_result = _run_text_mode(
                    config,
                    intent_engine,
                    system,
                    llm,
                    tts,
                    pipeline,
                    memory,
                    wake_detector,
                    terminal_state,
                    text_mode_state,
                    text_mode_toggle_event,
                    factory_reset_event,
                    new_conversation_event,
                    voice,
                    wave_renderer=wave_renderer,
                )
                if wave_renderer is not None:
                    wave_renderer.set_enabled(bool(config.get("waves_enabled", True)))
                    wave_renderer.set_style(config.get("ui_mode", "waves"))
                    if wave_renderer.enabled:
                        wave_renderer.set_mode("idle")
                if text_mode_result == "FACTORY_RESET":
                    result = _run_setup_flow(
                        config,
                        voice,
                        tts,
                        llm,
                        system,
                        memory,
                        terminal_state,
                        wake_detector,
                        factory_reset=True,
                        wave_renderer=wave_renderer,
                    )
                    if wave_renderer.enabled:
                        _prepare_terminal_for_text(wave_renderer=wave_renderer)
                    print(result)
                    _record_terminal_response(terminal_state, system, result)
                elif text_mode_result == "EXIT_ASSISTANT":
                    break
                continue

            if wave_renderer.enabled:
                wave_renderer.set_mode("idle")
            else:
                print("Listening for wake word...")
            heard_wake = wake_detector.listen_for_wake_word(
                stop_events=(factory_reset_event, text_mode_toggle_event, new_conversation_event),
                on_audio_level=wave_renderer.set_audio_level if wave_renderer.enabled else None,
            )
            if not heard_wake:
                continue

            if factory_reset_event.is_set():
                continue
            if new_conversation_event.is_set():
                continue

            if wave_renderer.enabled:
                wave_renderer.set_mode("wake")
                wave_renderer.pulse(1.0)
            if config.get("wake_response_enabled", True):
                wake_response = random_wake_response()
                _record_terminal_response(terminal_state, system, wake_response)
                tts.speak(wake_response, replace=True, interrupt=True)
                if not wave_renderer.enabled:
                    print(f"Assistant: {wake_response}")
            else:
                wake_response = "Wake word detected."
                _record_terminal_response(terminal_state, system, wake_response)
                if not wave_renderer.enabled:
                    print("Wake word detected.")

            if wave_renderer.enabled:
                wave_renderer.set_mode("recording")
            else:
                print("Listening for your command...")
            query_speaker_mode = wake_detector.query_speaker_mode()
            audio = voice.record_until_silence(
                max_duration=25.0,
                silence_duration=0.9,
                min_duration=0.45,
                start_timeout=4.8,
                fast_start=True,
                prefill_audio=wake_detector.consume_recent_audio(),
                speaker_reference=wake_detector.get_voice_reference(),
                speaker_mode=query_speaker_mode,
                on_speech_start=tts.stop,
                on_audio_chunk=wave_renderer.set_audio_level if wave_renderer.enabled else None,
            )
            if audio is None:
                # Fallback pass with looser speaker gate to avoid dropping the command.
                fallback_mode = "light" if query_speaker_mode == "strong" else query_speaker_mode
                audio = voice.record_until_silence(
                    max_duration=18.0,
                    silence_duration=0.85,
                    min_duration=0.35,
                    start_timeout=5.2,
                    fast_start=True,
                    speaker_reference=wake_detector.get_voice_reference(),
                    speaker_mode=fallback_mode,
                    on_speech_start=tts.stop,
                    on_audio_chunk=wave_renderer.set_audio_level if wave_renderer.enabled else None,
                )
                if audio is None:
                    if wave_renderer.enabled:
                        wave_renderer.set_mode("idle")
                    continue
            if wave_renderer.enabled:
                wave_renderer.set_mode("processing")
            else:
                print("Processing request...")
            query = (voice.transcribe(audio) or "").strip()
            if not query:
                retry_prompt = "I did not catch that. Please repeat."
                if config.get("wake_response_enabled", True):
                    tts.speak(retry_prompt, replace=True, interrupt=True)
                fallback_mode = "light" if query_speaker_mode == "strong" else query_speaker_mode
                audio_retry = voice.record_until_silence(
                    max_duration=18.0,
                    silence_duration=0.8,
                    min_duration=0.35,
                    start_timeout=5.0,
                    fast_start=True,
                    speaker_reference=wake_detector.get_voice_reference(),
                    speaker_mode=fallback_mode,
                    on_speech_start=tts.stop,
                    on_audio_chunk=wave_renderer.set_audio_level if wave_renderer.enabled else None,
                )
                query = (voice.transcribe(audio_retry) or "").strip() if audio_retry is not None else ""
            if not query and query_speaker_mode != "none":
                # Final pass without speaker gating; wake-word auth has already been checked.
                audio_retry = voice.record_until_silence(
                    max_duration=14.0,
                    silence_duration=0.75,
                    min_duration=0.28,
                    start_timeout=4.0,
                    fast_start=True,
                    speaker_mode="none",
                    on_speech_start=tts.stop,
                    on_audio_chunk=wave_renderer.set_audio_level if wave_renderer.enabled else None,
                )
                query = (voice.transcribe(audio_retry) or "").strip() if audio_retry is not None else ""
            if not query:
                if wave_renderer.enabled:
                    wave_renderer.set_mode("idle")
                continue

            result = _process_command(
                query,
                config=config,
                intent_engine=intent_engine,
                system=system,
                llm=llm,
                tts=tts,
                pipeline=pipeline,
                voice=voice,
                memory=memory,
                wake_detector=wake_detector,
                terminal_state=terminal_state,
                text_mode_state=text_mode_state,
                wave_renderer=wave_renderer,
            )
            if result == "ENTER_TEXT_MODE":
                text_mode_toggle_event.set()
            elif result == "EXIT_ASSISTANT":
                break
            if wave_renderer.enabled:
                wave_renderer.set_mode("idle")
    finally:
        wave_renderer.close()


if __name__ == "__main__":
    run()
