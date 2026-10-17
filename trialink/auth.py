"""Admin password check. Only a salted PBKDF2 hash is stored (ADMIN_PASSWORD_HASH in .env).

Generate a hash:  python -m trialink.auth
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ITERATIONS = 310_000
MAX_FAILURES = 5
LOCKOUT_S = 30


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.strip().split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


class Throttle:
    """Process-wide limit of failed logins (shared by all sessions)."""

    def __init__(self, max_failures: int = MAX_FAILURES, lockout_s: int = LOCKOUT_S):
        self.max_failures, self.lockout_s = max_failures, lockout_s
        self.failures: list[float] = []

    def locked_for(self, now: float | None = None) -> int:
        now = now or time.time()
        self.failures = [t for t in self.failures if now - t < self.lockout_s]
        if len(self.failures) >= self.max_failures:
            return int(self.lockout_s - (now - self.failures[0])) + 1
        return 0

    def fail(self, now: float | None = None) -> None:
        self.failures.append(now or time.time())

    def reset(self) -> None:
        self.failures.clear()


if __name__ == "__main__":
    from getpass import getpass

    pw = getpass("Nové heslo správce (min. 12 znaků): ")
    if len(pw) < 12:
        raise SystemExit("Heslo je příliš krátké.")
    if pw != getpass("Zopakujte heslo: "):
        raise SystemExit("Hesla se neshodují.")
    print("\nVložte do .env tento řádek:\nADMIN_PASSWORD_HASH=" + hash_password(pw))
