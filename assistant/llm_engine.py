import json
import os
import subprocess
import time

import requests


class LLMEngine:
    def __init__(self, online=True):
        self.online = bool(online)
        self.groq_api_key = self._normalize_env("GROQ_API_KEY")
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        self.groq_code_model = os.getenv("GROQ_CODE_MODEL", self.groq_model)
        self.groq_timeout = self._parse_int_env("GROQ_TIMEOUT_SEC", 35)

        self.openrouter_api_key = self._normalize_env("OPENROUTER_API_KEY")
        self.openrouter_model = os.getenv(
            "OPENROUTER_MODEL",
            "meta-llama/llama-3.3-8b-instruct:free",
        )
        self.openrouter_code_model = os.getenv(
            "OPENROUTER_CODE_MODEL",
            self.openrouter_model,
        )
        self.openrouter_url = os.getenv(
            "OPENROUTER_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.openrouter_timeout = self._parse_int_env("OPENROUTER_TIMEOUT_SEC", 35)

        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        self.local_model = os.getenv("OLLAMA_MODEL", "llama3.1:latest")
        self.local_code_model = os.getenv("OLLAMA_CODE_MODEL", self.local_model)
        self.ollama_timeout = self._parse_int_env("OLLAMA_TIMEOUT_SEC", 60)
        self._ollama_ready = False
        self._ollama_last_check = 0.0
        self._ollama_start_attempted = False
        self._online_cached = None
        self._online_last_check = 0.0

        self.language = "en"
        self.humor_level = 50

    def _normalize_env(self, key):
        value = os.getenv(key)
        if value is None:
            return None
        value = value.strip().strip('"').strip("'")
        return value or None

    def _parse_int_env(self, key, default):
        raw = self._normalize_env(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _ollama_tags_url(self):
        base = self.ollama_url
        if "/api/" in base:
            base = base.split("/api/", 1)[0]
        return base.rstrip("/") + "/api/tags"

    def _is_online(self):
        now = time.time()
        if self._online_cached is not None and (now - self._online_last_check) < 10:
            return bool(self._online_cached)
        self._online_last_check = now
        try:
            resp = requests.get("https://www.google.com/generate_204", timeout=2)
            ok = resp.status_code in {200, 204}
        except Exception:
            ok = False
        self._online_cached = ok
        return ok

    def _ensure_ollama_running(self):
        now = time.time()
        if self._ollama_ready and (now - self._ollama_last_check) < 30:
            return True
        self._ollama_last_check = now
        tags_url = self._ollama_tags_url()
        try:
            resp = requests.get(tags_url, timeout=3)
            if resp.ok:
                self._ollama_ready = True
                return True
        except Exception:
            pass
        self._ollama_ready = False
        if self._ollama_start_attempted:
            return False
        self._ollama_start_attempted = True
        try:
            kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen(["ollama", "serve"], **kwargs)
        except Exception:
            return False
        time.sleep(1.2)
        try:
            resp = requests.get(tags_url, timeout=5)
            if resp.ok:
                self._ollama_ready = True
                return True
        except Exception:
            return False
        return False

    def _build_system_prompt(self):
        parts = [
            "You are a personal desktop AI assistant similar to JARVIS, FRIDAY, and TARS.",
            "Respond only in English.",
            "Prefer intent and desktop context over literal surface wording.",
            "Treat spelling mistakes, vague phrasing, and speech recognition errors as recoverable.",
            "Keep answers direct and useful.",
        ]
        if self.humor_level >= 75:
            parts.append("Use dry, witty humor when appropriate.")
        elif self.humor_level >= 45:
            parts.append("Light humor is allowed when it does not obscure the answer.")
        return " ".join(parts)

    def _build_code_system_prompt(self):
        parts = [
            "You are a senior software engineer helping modify local codebases.",
            "Respond only in English.",
            "Prefer concrete, implementation-ready outputs.",
            "When asked for code updates, preserve unrelated behavior and keep patches coherent.",
        ]
        return " ".join(parts)

    @staticmethod
    def _extract_json(text):
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("Empty response.")

        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise ValueError("No JSON object found.")
        return json.loads(raw[start : end + 1])

    def _compose_local_prompt(self, prompt, *, system_prompt=None):
        system_prompt = system_prompt or self._build_system_prompt()
        return f"System: {system_prompt}\n\nUser: {prompt}\n\nAssistant:"

    def generate(self, prompt):
        if self.online and self._is_online() and self.groq_api_key:
            try:
                return self.groq_generate(prompt)
            except Exception as exc:
                print(f"Groq LLM failed ({exc}).")

        if self.online and self._is_online() and self.openrouter_api_key:
            try:
                return self.cloud_generate(prompt)
            except Exception as exc:
                print(f"OpenRouter LLM failed ({exc}). Falling back to local Ollama.")

        return self.local_generate(prompt)

    def generate_code(self, prompt):
        system_prompt = self._build_code_system_prompt()
        if self.online and self._is_online() and self.groq_api_key:
            try:
                return self.groq_generate(prompt, model=self.groq_code_model, system_prompt=system_prompt)
            except Exception as exc:
                print(f"Groq code LLM failed ({exc}).")

        if self.online and self._is_online() and self.openrouter_api_key:
            try:
                return self.cloud_generate(prompt, model=self.openrouter_code_model, system_prompt=system_prompt)
            except Exception as exc:
                print(f"OpenRouter code LLM failed ({exc}). Falling back to local coding model.")

        return self.local_generate(prompt, model=self.local_code_model, system_prompt=system_prompt)

    def generate_json(self, prompt):
        response = self.generate(prompt)
        return self._extract_json(response)

    def groq_generate(self, prompt, *, model=None, system_prompt=None):
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or self.groq_model,
                "messages": [
                    {"role": "system", "content": system_prompt or self._build_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=self.groq_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text}")

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def cloud_generate(self, prompt, *, model=None, system_prompt=None):
        response = requests.post(
            self.openrouter_url,
            headers={
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Local Assistant",
            },
            json={
                "model": model or self.openrouter_model,
                "messages": [
                    {"role": "system", "content": system_prompt or self._build_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=self.openrouter_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text}")

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def local_generate(self, prompt, *, model=None, system_prompt=None):
        model_name = model or self.local_model
        if not self._ensure_ollama_running():
            raise RuntimeError("Ollama server is not reachable. Start the Ollama app or run `ollama serve`.")
        response = requests.post(
            self.ollama_url,
            json={
                "model": model_name,
                "prompt": self._compose_local_prompt(prompt, system_prompt=system_prompt),
                "stream": False,
            },
            timeout=self.ollama_timeout,
        )
        if response.status_code != 200:
            if response.status_code == 404 and model_name.endswith(":latest"):
                alt_model = model_name.split(":", 1)[0]
                retry = requests.post(
                    self.ollama_url,
                    json={
                        "model": alt_model,
                        "prompt": self._compose_local_prompt(prompt, system_prompt=system_prompt),
                        "stream": False,
                    },
                    timeout=self.ollama_timeout,
                )
                if retry.status_code == 200:
                    data = retry.json()
                    text = data.get("response", "").strip()
                    if text:
                        return text
                response = retry
            if response.status_code == 404:
                raise RuntimeError(
                    f"Ollama model '{model_name}' not found. Run `ollama pull {model_name}`."
                )
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text}")

        data = response.json()
        text = data.get("response", "").strip()
        if not text:
            raise RuntimeError("Ollama returned an empty response.")
        return text

    def stream_generate(self, prompt):
        if self.online and self._is_online() and self.groq_api_key:
            try:
                yield from self.groq_stream(prompt)
                return
            except Exception as exc:
                print(f"Groq stream failed ({exc}).")

        if self.online and self._is_online() and self.openrouter_api_key:
            try:
                yield from self.cloud_stream(prompt)
                return
            except Exception as exc:
                print(f"OpenRouter stream failed ({exc}). Falling back to local Ollama.")

        yield from self.local_stream(prompt)

    def groq_stream(self, prompt):
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.groq_model,
                "messages": [
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
            },
            stream=True,
            timeout=max(self.groq_timeout, 45),
        )
        if response.status_code != 200:
            raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text}")

        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8").strip()
            if not decoded.startswith("data:"):
                continue

            payload = decoded.replace("data: ", "", 1)
            if payload == "[DONE]":
                break

            data = json.loads(payload)
            choices = data.get("choices", [])
            if not choices:
                continue
            token = choices[0].get("delta", {}).get("content")
            if token:
                yield token

    def cloud_stream(self, prompt):
        response = requests.post(
            self.openrouter_url,
            headers={
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Local Assistant",
            },
            json={
                "model": self.openrouter_model,
                "messages": [
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
            },
            stream=True,
            timeout=max(self.openrouter_timeout, 45),
        )
        if response.status_code != 200:
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text}")

        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8").strip()
            if not decoded.startswith("data:"):
                continue

            payload = decoded.replace("data: ", "", 1)
            if payload == "[DONE]":
                break

            data = json.loads(payload)
            choices = data.get("choices", [])
            if not choices:
                continue
            token = choices[0].get("delta", {}).get("content")
            if token:
                yield token

    def local_stream(self, prompt):
        if not self._ensure_ollama_running():
            raise RuntimeError("Ollama server is not reachable. Start the Ollama app or run `ollama serve`.")
        response = requests.post(
            self.ollama_url,
            json={
                "model": self.local_model,
                "prompt": self._compose_local_prompt(prompt),
                "stream": True,
            },
            stream=True,
            timeout=self.ollama_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text}")

        for line in response.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            token = data.get("response")
            if token:
                yield token
