import argparse
import sys
import time

import requests


def _request(method, url, json_payload=None, timeout=6):
    response = requests.request(method, url, json=json_payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def run(base_url, timeout=6):
    status_url = f"{base_url}/api/status"
    command_url = f"{base_url}/api/command"

    print(f"[smoke] Checking {status_url}")
    status = _request("GET", status_url, timeout=timeout)
    if status.get("status") != "ok":
        print("[smoke] Bridge did not return ok status.")
        return 1

    commands = [
        {"text": "list history"},
        {"text": "set humor to 40 percent"},
        {"text": "what can you do"},
    ]

    for payload in commands:
        print(f"[smoke] POST {payload['text']!r}")
        result = _request("POST", command_url, json_payload=payload, timeout=timeout)
        print(f"[smoke] -> {result.get('response')}")
        time.sleep(0.2)

    print("[smoke] OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Smoke test the assistant GUI bridge.")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Base URL for the GUI bridge.")
    parser.add_argument("--timeout", type=int, default=6, help="Request timeout seconds.")
    args = parser.parse_args()

    try:
        code = run(args.url.rstrip("/"), timeout=args.timeout)
    except requests.RequestException as exc:
        print(f"[smoke] Failed: {exc}")
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
