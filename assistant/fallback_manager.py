import socket


class FallbackManager:
    def __init__(self):
        self.online = self.check_internet()

    def check_internet(self):
        candidates = [
            ("1.1.1.1", 53),
            ("8.8.8.8", 53),
            ("208.67.222.222", 53),
        ]
        for host, port in candidates:
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                continue
        return False
