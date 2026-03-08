from setuptools import setup, find_packages

setup(
    name="assistant",
    version="1.0",
    packages=find_packages(),
    install_requires=[
        "faster-whisper",
        "vosk",
        "sounddevice",
        "torch",
        "requests",
        "edge-tts",
        "playsound==1.2.2",
        "pyttsx3",
        "rich",
        "textual",
        "keyboard",
        "numpy",
        "pyyaml",
        "scipy",
        "piper-tts",
        'pywin32; platform_system == "Windows"',
    ],
    entry_points={
        "console_scripts": [
            "assistant=assistant.main:run"
        ]
    },
)
