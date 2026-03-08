"""
Voice fingerprint authentication for the assistant.

The enrollment flow captures multiple samples from the authorized user and
stores an averaged fingerprint. Verification compares a new sample against that
fingerprint using cosine similarity.
"""

from pathlib import Path

import difflib
import numpy as np


_VOICE_DIR = Path.home() / ".assistant"
_DEFAULT_SAMPLE_PATH = _VOICE_DIR / "voice_fingerprint.npy"
_SAMPLE_SENTENCE = "Hello assistant, this is my voice authentication sample."


def _extract_features(audio_int16):
    audio = audio_int16.astype(np.float32) / 32768.0
    if audio.size == 0:
        return np.zeros(12, dtype=np.float32)

    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))
    zcr = float(np.sum(np.abs(np.diff(np.sign(audio))) > 0)) / max(len(audio), 1)

    fft_mag = np.abs(np.fft.rfft(audio))
    freqs = np.linspace(0.0, 1.0, len(fft_mag), dtype=np.float32)
    total = float(fft_mag.sum()) or 1.0
    centroid = float(np.sum(freqs * fft_mag) / total)
    spread = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * fft_mag) / total))
    rolloff_idx = np.searchsorted(np.cumsum(fft_mag), total * 0.85)
    rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])

    windows = 24
    win_size = max(1, len(audio) // windows)
    energies = []
    for index in range(windows):
        chunk = audio[index * win_size : (index + 1) * win_size]
        if len(chunk):
            energies.append(float(np.mean(chunk**2)))
    energy_var = float(np.var(energies)) if energies else 0.0
    energy_mean = float(np.mean(energies)) if energies else 0.0

    quarters = []
    for ratio in (0.25, 0.5, 0.75):
        boundary = int(len(audio) * ratio)
        quarters.append(float(np.sqrt(np.mean(audio[: max(boundary, 1)] ** 2))))

    return np.array(
        [
            rms,
            peak,
            zcr,
            centroid,
            spread,
            rolloff,
            energy_mean,
            energy_var,
            *quarters,
            float(len(audio) / 16000.0),
        ],
        dtype=np.float32,
    )


def _cosine_similarity(a, b):
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _required_similarity(threshold):
    level = max(0, min(100, int(threshold)))
    return 0.45 + (level / 100.0) * 0.45


def enroll_voice(voice_engine, save_path=None, strict=True):
    save_path = Path(save_path) if save_path else _DEFAULT_SAMPLE_PATH
    save_path.parent.mkdir(parents=True, exist_ok=True)

    print("\n=== Voice Authentication Setup ===")
    print(f'Please read the following sentence aloud:\n\n  "{_SAMPLE_SENTENCE}"\n')

    fingerprints = []
    for sample_index in range(1, 4):
        input(f"Press ENTER to record sample {sample_index}/3 ... ")
        audio = voice_engine.record_until_silence(max_duration=10.0, silence_duration=1.2, min_duration=2.0)
        if audio is None or len(audio) == 0:
            print("No audio captured for this sample.")
            continue
        if strict:
            transcript = voice_engine.transcribe(audio) or ""
            ratio = difflib.SequenceMatcher(None, transcript.lower(), _SAMPLE_SENTENCE.lower()).ratio()
            if ratio < 0.60:
                print(f"Sample {sample_index} did not match the sentence closely enough. Heard: '{transcript}'")
                continue
        fingerprints.append(_extract_features(audio))

    if not fingerprints:
        print("Voice enrollment failed.")
        return ""

    merged = np.mean(np.vstack(fingerprints), axis=0)
    np.save(str(save_path), merged)
    print(f"Voice fingerprint saved to {save_path}\n")
    return str(save_path)


def verify_voice(voice_engine, threshold, sample_path=None):
    threshold = int(threshold or 0)
    if threshold <= 0:
        return True

    sample = Path(sample_path) if sample_path else _DEFAULT_SAMPLE_PATH
    if not sample.exists():
        print("No stored voice fingerprint was found.")
        return False

    enrolled = np.load(str(sample))
    audio = voice_engine.record_until_silence(max_duration=5.0, silence_duration=0.8, min_duration=1.0, start_timeout=2.5)
    if audio is None or len(audio) == 0:
        print("No audio captured for verification.")
        return False

    current = _extract_features(audio)
    similarity = _cosine_similarity(enrolled, current)
    required = _required_similarity(threshold)
    passed = similarity >= required
    if passed:
        print(f"Voice authenticated (similarity {similarity:.2f}, required {required:.2f}).")
    else:
        print(f"Voice authentication failed (similarity {similarity:.2f}, required {required:.2f}).")
    return passed


def get_sample_sentence():
    return _SAMPLE_SENTENCE
