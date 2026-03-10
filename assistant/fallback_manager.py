import socket
import os


class FallbackManager:
    def __init__(self):
        self.online = self.check_internet()

    def check_internet(self):
        timeout = self._parse_timeout_env("ASSISTANT_NET_CHECK_TIMEOUT", 0.45)
        candidates = [
            ("1.1.1.1", 53),
            ("8.8.8.8", 53),
            ("208.67.222.222", 53),
        ]
        for host, port in candidates:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _parse_timeout_env(key, default):
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            value = float(str(raw).strip())
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default
