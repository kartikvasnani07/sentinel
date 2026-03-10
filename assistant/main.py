import os
import pathlib
import re
import sys
import threading
import time

import keyboard

try:
    import msvcrt
except ImportError:
    msvcrt = None

from .bootstrap import run_first_time_setup
from .config import AssistantConfig, VOICE_PRESETS
from .fallback_manager import FallbackManager
from .intent_engine import IntentEngine
from .llm_engine import LLMEngine
from .memory import Memory
from .security import prompt_password_check, prompt_password_reset
from .streaming_pipeline import StreamingPipeline
from .system_actions import SystemActions
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


def _normalize_text(text):
    return " ".join(str(text or "").lower().replace("'", "").split())


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
        selected_text = _prompt_choice("Multiple close media files were found. Choose one to open:", [str(item) for item in global_candidates])
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


def _speak_with_barge_in(tts, text, *, wake_detector=None, voice=None, timeout=8.0):
    if not text:
        return ""
    tts.speak(text, replace=True, interrupt=True)
    if wake_detector is None or voice is None:
        tts.wait_until_done(timeout=timeout)
        return ""
    while tts.is_speaking():
        if wake_detector.listen_for_wake_word(timeout=1.1):
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
            )
            transcript = voice.transcribe(audio) if audio is not None else ""
            return transcript or "__INTERRUPTED__"
    tts.wait_until_done(timeout=timeout)
    return ""


def _prompt_yes_no(question):
    answer = input(question).strip().lower()
    return answer in {"yes", "y"}


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
    if is_text_mode or voice is None:
        return _prompt_yes_no(f"{prompt} (yes/no): ")

    print(f"Assistant: {prompt}")
    tts.speak(prompt, replace=True, interrupt=True)
    tts.wait_until_done(timeout=6.0)
    for _ in range(2):
        audio = voice.record_until_silence(max_duration=4.0, silence_duration=0.6, min_duration=0.2, start_timeout=3.0)
        transcript = voice.transcribe(audio) if audio is not None else ""
        if transcript:
            print(f"User: {transcript}")
        interpreted = _interpret_yes_no(transcript)
        if interpreted is not None:
            return interpreted
        retry_prompt = "Please say yes or no."
        print(f"Assistant: {retry_prompt}")
        tts.speak(retry_prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=4.0)
    return False


def _prompt_choice(question, options):
    print(question)
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    answer = input("Enter the number or exact file name (blank to cancel): ").strip()
    if not answer:
        return ""
    if answer.isdigit():
        index = int(answer) - 1
        if 0 <= index < len(options):
            return options[index]
    return answer


def _print_voice_preset_options():
    print("\nAvailable voice models:")
    for index, (name, preset) in enumerate(VOICE_PRESETS.items(), start=1):
        description = preset.get("description", name).strip()
        print(f"  {index}. {name} - {description}")


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


def _setup_voice_model_interactively(config, tts):
    _print_voice_preset_options()
    while True:
        choice = input("Voice model (name/number, 'list', blank to cancel): ").strip().lower()
        if not choice:
            return False, "Voice model setup cancelled."
        if choice in {"list", "show", "help", "?"}:
            _print_voice_preset_options()
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
        confirm = input("Use this voice model? (yes/no): ").strip().lower()
        if confirm in {"yes", "y"}:
            config.set("voice_preset", preset)
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
            selected = _prompt_choice("Multiple source matches found. Choose the exact source path:", [str(item) for item in candidates])
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
        selected = _prompt_choice("Multiple matching paths were found. Choose the exact one to delete:", choices)
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
        selected = _prompt_choice(f"Multiple close matches were found. Choose the exact path to {purpose}:", choices)
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


def _resolve_create_request(system, params):
    requested_name = str(params.get("name") or "notes.txt").strip()
    directory = params.get("directory")
    if not directory:
        selected_path = input(f"Enter a directory path for '{requested_name}' (blank for Home): ").strip()
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
    if not _prompt_yes_no(f"Create {target_name}? (yes/no): "):
        return None, f"Creation cancelled for {target_name}."
    return params, ""


def _prompt_for_name(tts, voice, is_text_mode):
    if not is_text_mode and voice is not None:
        prompt = "What should I call myself?"
        print(f"Assistant: {prompt}")
        tts.speak(prompt, replace=True, interrupt=True)
        tts.wait_until_done(timeout=3.0)
        audio = voice.record_until_silence(max_duration=5.0, silence_duration=0.8, min_duration=0.5, start_timeout=3.0)
        if audio is not None:
            transcript = voice.transcribe(audio) or ""
            name = _sanitize_assistant_name(transcript)
            if name:
                return name
    typed = input("Enter the new assistant name: ").strip()
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


def _run_setup_flow(config, voice, tts, llm, system, memory, terminal_state, wake_detector, *, factory_reset=False):
    if factory_reset:
        config.reset_factory_state(delete_voice_sample=True)
    run_first_time_setup(config, voice_engine=voice, tts=tts)
    llm.language = "en"
    llm.humor_level = config.get("humor_level", 50)
    tts.apply_voice_preset(config.get("voice_preset", "jarvis"))
    system.clear_context()
    memory.clear()
    terminal_state["last_response"] = ""
    wake_detector.update_wake_word(config.get("assistant_name", "friday"), config.get("wake_variants", []))
    wake_detector.set_voice_reference(
        config.get("voice_sample_path", ""),
        config.get("voice_auth_threshold", 0),
    )
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
):
    context = dict(system.session_context)
    intent_data = intent_engine.detect(query, context=context)
    intent = intent_data.get("intent", "conversation")
    action = intent_data.get("action")
    params = intent_data.get("parameters", {})
    can_speak = (not is_text_mode) or bool(text_mode_state.get("voice_enabled"))

    if action == "enter_text_mode":
        return "ENTER_TEXT_MODE" if not is_text_mode else None

    if action == "new_conversation":
        result = _open_new_conversation(memory, system, terminal_state)
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "list_history":
        result = _format_history(memory)
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, "History listed on the terminal.", wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
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
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "delete_conversation":
        conversation_id = params.get("conversation_id") or input("Enter conversation id to delete: ").strip()
        normalized = str(conversation_id or "").strip()
        if not normalized:
            result = "No conversation id was provided."
            print(result)
            _record_terminal_response(terminal_state, system, result)
            return None
        if not _confirm_prompt(f"Delete conversation {normalized}?", tts=tts, voice=voice, is_text_mode=is_text_mode):
            result = "Conversation deletion cancelled."
            print(result)
            _record_terminal_response(terminal_state, system, result)
            return None
        ok, result = memory.delete_conversation(normalized)
        if ok:
            system.clear_context()
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "stop_assistant":
        result = "Shutting down the assistant."
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            tts.speak(result, replace=True, interrupt=True)
            tts.wait_until_done(timeout=4.0)
        return "EXIT_ASSISTANT"

    if action == "restart_setup":
        result = _run_setup_flow(config, voice, tts, llm, system, memory, terminal_state, wake_detector, factory_reset=False)
        print(result)
        _record_terminal_response(terminal_state, system, result)
        _preview_active_voice(config, tts)
        return None

    if action == "change_assistant_name":
        new_name = params.get("assistant_name") or _prompt_for_name(tts, voice, is_text_mode)
        result = _apply_name_change(config, wake_detector, new_name)
        print(result)
        _record_terminal_response(terminal_state, system, result)
        follow_up = ""
        if can_speak:
            follow_up = _speak_with_barge_in(
                tts,
                result,
                wake_detector=wake_detector if not is_text_mode else None,
                voice=voice if not is_text_mode else None,
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
        )

    if action == "clear_history":
        active_id = memory.current_conversation_id()
        memory.clear()
        system.clear_context()
        terminal_state["last_response"] = ""
        result = f"Cleared conversation {active_id}."
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "read_terminal":
        text = terminal_state.get("last_response") or "There is nothing on the terminal to read."
        print(text)
        _record_terminal_response(terminal_state, system, text)
        _speak_with_barge_in(tts, text, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None, timeout=15.0)
        return None

    if action == "reset_password":
        if prompt_password_reset(config):
            result = "Password has been reset."
            print(result)
            _record_terminal_response(terminal_state, system, result)
            if can_speak:
                _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
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
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "set_humor":
        level = params.get("level", 50)
        config.set("humor_level", level)
        llm.humor_level = level
        result = f"Humor level set to {level} percent."
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "change_language":
        requested = str(params.get("language") or "english").strip().lower()
        result = "English is active." if requested in {"", "en", "english", "default"} else "English is currently the only supported language in this build."
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "change_voice":
        preset = str(params.get("preset") or "").strip().lower()
        if not preset:
            applied, result = _setup_voice_model_interactively(config, tts)
        else:
            result = tts.apply_voice_preset(preset)
            if "Unknown voice preset" not in result:
                config.set("voice_preset", preset)
                _preview_active_voice(config, tts)
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "set_autostart":
        result = enable_autostart() if params.get("on", True) else disable_autostart()
        config.set("auto_start", bool(params.get("on", True)))
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
        return None

    if action == "set_wake_response":
        enabled = bool(params.get("on", True))
        config.set("wake_response_enabled", enabled)
        result = "Wake-word response enabled." if enabled else "Wake-word response disabled."
        print(result)
        _record_terminal_response(terminal_state, system, result)
        if can_speak:
            _speak_with_barge_in(tts, result, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None)
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
                print(routed_error)
                _record_terminal_response(terminal_state, system, routed_error)
                if can_speak:
                    _speak_with_barge_in(
                        tts,
                        routed_error,
                        wake_detector=wake_detector if not is_text_mode else None,
                        voice=voice if not is_text_mode else None,
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
                        print(result)
                        _record_terminal_response(terminal_state, system, result)
                        return None
                    if not _confirm_prompt(
                        f"Apply corrections to {target.name} for this purpose?",
                        tts=tts,
                        voice=voice,
                        is_text_mode=is_text_mode,
                    ):
                        result = "Project edit cancelled."
                        print(result)
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
                        print(result)
                        _record_terminal_response(terminal_state, system, result)
                        return None
                    params["instruction"] = follow_instruction

        if action in {"open_application", "close_application", "run_as_admin"} or (action == "open_path" and params.get("application")):
            app_match = system.resolve_application_request(params)
            if app_match.get("status") == "missing":
                result = f"I could not find an installed application matching {app_match.get('requested') or params.get('application') or 'that request'}."
                print(result)
                _record_terminal_response(terminal_state, system, result)
                if can_speak:
                    _speak_with_barge_in(
                        tts,
                        result,
                        wake_detector=wake_detector if not is_text_mode else None,
                        voice=voice if not is_text_mode else None,
                    )
                return None
            if app_match.get("status") == "needs_confirmation":
                prompt = (
                    f"I could not find {app_match.get('requested')} exactly. "
                    f"Do you want me to open {app_match.get('display_name')} instead?"
                )
                if not _confirm_prompt(prompt, tts=tts, voice=voice, is_text_mode=is_text_mode):
                    result = "Application action cancelled."
                    print(result)
                    _record_terminal_response(terminal_state, system, result)
                    return None
            params["resolved_application"] = app_match

        if action == "create_file":
            resolved, error = _resolve_create_request(system, dict(params))
            if not resolved:
                print(error)
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
                print(error)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if action in {"copy_path", "move_path", "rename_path", "duplicate_path"}:
            resolved, error = _resolve_transfer_request(system, action, dict(params), tts=tts, voice=voice, is_text_mode=is_text_mode)
            if not resolved:
                print(error)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        if action == "delete_path":
            resolved, error = _resolve_delete_request(system, dict(params), tts=tts, voice=voice, is_text_mode=is_text_mode)
            if not resolved:
                print(error)
                _record_terminal_response(terminal_state, system, error)
                return None
            params = resolved

        result = system.execute(action, params)
        print(result)
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
            )
        return None

    memory.add("user", query)
    prompt = f"Conversation so far:\n{memory.get_context()}\n\nUser: {query}"
    response = pipeline.run(prompt, speak=False)
    memory.add("assistant", response)
    _record_terminal_response(terminal_state, system, response)
    media = _extract_media_request_from_text(response)
    if media:
        system.session_context["last_media_request"] = media
    if can_speak:
        follow_up = _speak_with_barge_in(tts, response, wake_detector=wake_detector if not is_text_mode else None, voice=voice if not is_text_mode else None, timeout=12.0)
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
        )
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
):
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
                _speak_with_barge_in(tts, response, timeout=6.0)
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
                voice=None,
                memory=memory,
                wake_detector=wake_detector,
                terminal_state=terminal_state,
                text_mode_state=text_mode_state,
                is_text_mode=True,
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
                _speak_with_barge_in(tts, response, timeout=6.0)
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
                voice=None,
                memory=memory,
                wake_detector=wake_detector,
                terminal_state=terminal_state,
                text_mode_state=text_mode_state,
                is_text_mode=True,
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

    tts.apply_voice_preset(config.get("voice_preset", "jarvis"))
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
    )
    wake_detector.set_voice_reference(
        config.get("voice_sample_path", ""),
        config.get("voice_auth_threshold", 0),
    )
    if sorted(config.get("wake_variants", [])) != sorted(wake_detector.wake_variants):
        config.set("wake_variants", sorted(wake_detector.wake_variants))

    text_mode_toggle_event = threading.Event()
    factory_reset_event = threading.Event()
    new_conversation_event = threading.Event()
    keyboard.add_hotkey("ctrl+space", text_mode_toggle_event.set)
    keyboard.add_hotkey("ctrl+shift+r", factory_reset_event.set)
    keyboard.add_hotkey("ctrl+shift+alt", new_conversation_event.set)

    greeting = random_greeting()
    print(f"\nAssistant: {greeting}\n")
    _record_terminal_response(terminal_state, system, greeting)
    _speak_with_barge_in(tts, greeting, wake_detector=wake_detector, voice=voice)

    while True:
        if factory_reset_event.is_set():
            factory_reset_event.clear()
            tts.stop()
            result = _run_setup_flow(config, voice, tts, llm, system, memory, terminal_state, wake_detector, factory_reset=True)
            print(result)
            _record_terminal_response(terminal_state, system, result)
            continue

        if new_conversation_event.is_set():
            new_conversation_event.clear()
            result = _open_new_conversation(memory, system, terminal_state)
            print(result)
            _record_terminal_response(terminal_state, system, result)
            continue

        if text_mode_toggle_event.is_set():
            text_mode_toggle_event.clear()
            if config.get("password_hash") and not prompt_password_check(config.get("password_hash"), purpose="enter text mode"):
                print("Access denied for text mode.")
                continue
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
            )
            if text_mode_result == "FACTORY_RESET":
                result = _run_setup_flow(config, voice, tts, llm, system, memory, terminal_state, wake_detector, factory_reset=True)
                print(result)
                _record_terminal_response(terminal_state, system, result)
            elif text_mode_result == "EXIT_ASSISTANT":
                break
            continue

        print("Listening for wake word...")
        heard_wake = wake_detector.listen_for_wake_word(
            stop_events=(factory_reset_event, text_mode_toggle_event, new_conversation_event),
        )
        if not heard_wake:
            continue

        if factory_reset_event.is_set():
            continue
        if new_conversation_event.is_set():
            continue

        if config.get("wake_response_enabled", True):
            wake_response = random_wake_response()
            print(f"Assistant: {wake_response}")
            _record_terminal_response(terminal_state, system, wake_response)
            tts.speak(wake_response, replace=True, interrupt=True)
        else:
            wake_response = "Wake word detected."
            print(wake_response)
            _record_terminal_response(terminal_state, system, wake_response)

        print("Listening for your command...")
        audio = voice.record_until_silence(
            max_duration=25.0,
            silence_duration=1.0,
            min_duration=0.8,
            start_timeout=6.0,
            fast_start=True,
            prefill_audio=wake_detector.consume_recent_audio(),
            speaker_reference=wake_detector.get_voice_reference(),
            speaker_mode=wake_detector.query_speaker_mode(),
            on_speech_start=tts.stop,
        )
        if audio is None:
            continue
        query = voice.transcribe(audio)
        if not query:
            continue

        print(f"\nUser: {query}")
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
        )
        if result == "ENTER_TEXT_MODE":
            text_mode_toggle_event.set()
        elif result == "EXIT_ASSISTANT":
            break


if __name__ == "__main__":
    run()
