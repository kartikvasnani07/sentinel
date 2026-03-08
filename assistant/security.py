"""
Password authentication layer for the assistant.

Passwords are stored as PBKDF2-SHA256 hashes. Legacy plain SHA-256 hashes are
still accepted for migration compatibility.
"""

import getpass
import hashlib
import hmac
import secrets


PBKDF2_PREFIX = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 240_000


def _legacy_hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt}${derived}"


def verify_password(plain: str, stored_hash: str) -> bool:
    stored = str(stored_hash or "").strip()
    if not stored:
        return False

    if stored.startswith(f"{PBKDF2_PREFIX}$"):
        try:
            _, iterations_raw, salt, expected = stored.split("$", 3)
            iterations = int(iterations_raw)
        except ValueError:
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            plain.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(derived, expected)

    return hmac.compare_digest(_legacy_hash_password(plain), stored)


def prompt_password_setup() -> str:
    print("\n=== Password Setup ===")
    while True:
        password = getpass.getpass("Create a password: ")
        if len(password) < 4:
            print("Password must be at least 4 characters long.")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match. Try again.")
            continue
        print("Password set successfully.\n")
        return hash_password(password)


def prompt_password_check(stored_hash: str, purpose: str = "start the assistant") -> bool:
    attempts = 3
    print(f"\nPlease enter your password to {purpose}.")
    for attempt in range(1, attempts + 1):
        password = getpass.getpass(f"Password (attempt {attempt}/{attempts}): ")
        if verify_password(password, stored_hash):
            return True
        print("Incorrect password.")
    print("Access denied.\n")
    return False


def prompt_password_reset(config) -> bool:
    stored_hash = config.get("password_hash", "")
    if stored_hash:
        current = getpass.getpass("Enter your current password: ")
        if not verify_password(current, stored_hash):
            print("Current password is incorrect. Reset aborted.")
            return False
    new_hash = prompt_password_setup()
    config.set("password_hash", new_hash)
    print("Password has been reset.\n")
    return True
