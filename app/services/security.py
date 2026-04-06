"""Security primitives for passwords, sessions and encrypted secrets."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecurityService:
    """Provide reusable security helpers for web auth and secret storage."""

    _password_prefix = "scrypt"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._fernet = Fernet(self._build_fernet_key())

    def hash_password(self, password: str) -> str:
        """Hash a password using scrypt with a random salt."""
        password_bytes = self._validate_password(password)
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password_bytes, salt=salt, n=2**14, r=8, p=1, dklen=64)
        salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
        derived_b64 = base64.urlsafe_b64encode(derived).decode("ascii")
        return f"{self._password_prefix}${salt_b64}${derived_b64}"

    def verify_password(self, password: str, password_hash: str | None) -> bool:
        """Verify a password against a stored scrypt hash."""
        if not password_hash:
            return False

        try:
            algorithm, salt_b64, expected_b64 = password_hash.split("$", maxsplit=2)
        except ValueError:
            return False

        if algorithm != self._password_prefix:
            return False

        try:
            salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
            expected = base64.urlsafe_b64decode(expected_b64.encode("ascii"))
        except Exception:
            return False

        candidate = hashlib.scrypt(
            self._validate_password(password),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=64,
        )
        return hmac.compare_digest(candidate, expected)

    def generate_web_session_token(self) -> str:
        """Generate a browser session token for a user."""
        return secrets.token_urlsafe(32)

    def hash_web_session_token(self, token: str) -> str:
        """Hash a session token before storing it in the database."""
        normalized = token.strip()
        if not normalized:
            raise ValueError("Session token is required.")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def web_session_expiry(self, now: datetime | None = None) -> datetime:
        """Compute the expiry timestamp for a new web session."""
        base = now or datetime.now()
        return base + timedelta(hours=self.settings.effective_web_session_ttl_hours)

    def encrypt_api_key(self, value: str) -> str:
        """Encrypt a provider API key for storage."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("API key is required.")
        return self._fernet.encrypt(normalized.encode("utf-8")).decode("ascii")

    def decrypt_api_key(self, encrypted_value: str) -> str:
        """Decrypt an encrypted provider API key."""
        try:
            decrypted = self._fernet.decrypt(encrypted_value.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("Encrypted API key is invalid.") from exc
        return decrypted.decode("utf-8")

    def mask_api_key(self, value: str) -> str:
        """Mask a secret value for safe display."""
        normalized = value.strip()
        if len(normalized) <= 4:
            return "*" * len(normalized)
        return f"{'*' * (len(normalized) - 4)}{normalized[-4:]}"

    def key_last4(self, value: str) -> str:
        """Return the last four characters of a secret."""
        normalized = value.strip()
        return normalized[-4:] if normalized else ""

    def _build_fernet_key(self) -> bytes:
        """Derive a Fernet-compatible key from configured key material."""
        material = self.settings.effective_app_encryption_key_material.encode("utf-8")
        derived = hashlib.sha256(material).digest()
        return base64.urlsafe_b64encode(derived)

    def _validate_password(self, password: str) -> bytes:
        """Validate and normalize a plaintext password."""
        normalized = password.strip()
        if len(normalized) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        return normalized.encode("utf-8")
