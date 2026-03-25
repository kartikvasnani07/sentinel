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
        "yt-dlp",
        "edge-tts",
        "playsound==1.2.2",
        "pyttsx3",
        "soundfile",
        "keyboard",
        "numpy",
        "pyyaml",
        "piper-tts",
        'pycaw; platform_system == "Windows"',
        'comtypes; platform_system == "Windows"',
        'pywin32; platform_system == "Windows"',
    ],
    entry_points={
        "console_scripts": [
            "assistant=assistant.main:run",
            "assistant-install=assistant.installer:main",
            "assistant-gui=assistant.gui_server:run",
            "assistant-smoke=scripts.smoke_gui_bridge:main",
        ]
    },
)
