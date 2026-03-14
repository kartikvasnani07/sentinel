"""
OpenVoice inference wrapper for custom voice cloning.

This wrapper expects an external OpenVoice inference command to be provided via
the OPENVOICE_INFER_CMD environment variable.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def _split_command(command: str) -> list[str]:
    command = str(command or "").strip()
    if not command:
        return []
    return shlex.split(command, posix=os.name != "nt")


class OpenVoiceEngine:
    def __init__(self, *, model_path: str | None = None, device: str | None = None) -> None:
        self.cmd_template = os.getenv("OPENVOICE_INFER_CMD", "").strip()
        self.extra_args = os.getenv("OPENVOICE_INFER_ARGS", "").strip()
        if not self.cmd_template:
            raise RuntimeError(
                "OPENVOICE_INFER_CMD is not set. Configure it to point to your OpenVoice inference command."
            )

        self.model_path = str(model_path or "").strip()
        self.device = (device or os.getenv("OPENVOICE_DEVICE", "") or "cpu").strip().lower()
        if self.device not in {"cpu", "cuda"}:
            self.device = "cpu"

    @staticmethod
    def is_available() -> bool:
        return bool(os.getenv("OPENVOICE_INFER_CMD", "").strip())

    def _build_command(self, *, text: str, text_file: Path, speaker_path: Path, output_path: Path) -> list[str]:
        data = {
            "text": text,
            "text_file": str(text_file),
            "speaker": str(speaker_path),
            "output": str(output_path),
            "device": self.device,
            "model": self.model_path,
        }

        if "{" in self.cmd_template and "}" in self.cmd_template:
            cmd_str = self.cmd_template.format(**data)
            args = _split_command(cmd_str)
        else:
            args = _split_command(self.cmd_template)
            if self.model_path:
                args.extend(["--model", self.model_path])
            args.extend(
                [
                    "--text-file",
                    data["text_file"],
                    "--speaker",
                    data["speaker"],
                    "--output",
                    data["output"],
                    "--device",
                    data["device"],
                ]
            )

        if self.extra_args:
            args.extend(_split_command(self.extra_args))
        return args

    def synthesize(self, text: str, speaker_path: str | Path, output_path: str | Path) -> None:
        speaker_path = Path(speaker_path).expanduser().resolve()
        if not speaker_path.exists():
            raise RuntimeError(f"OpenVoice speaker sample was not found: {speaker_path}")
        output_path = Path(output_path).expanduser().resolve()

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write(text)
            text_file = Path(handle.name)

        try:
            command = self._build_command(
                text=text,
                text_file=text_file,
                speaker_path=speaker_path,
                output_path=output_path,
            )
            if not command:
                raise RuntimeError("OpenVoice inference command is empty.")
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()
                detail = stderr or stdout or "Unknown OpenVoice error."
                detail = detail.replace("\n", " ").strip()
                if len(detail) > 300:
                    detail = detail[:297] + "..."
                raise RuntimeError(f"OpenVoice conversion failed: {detail}")
        finally:
            try:
                text_file.unlink(missing_ok=True)
            except Exception:
                pass
