"""Validation utilities."""

import re


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number by removing non-digits.

    Examples:
        "+55 11 99999-9999" -> "5511999999999"
        "5511999999999@s.whatsapp.net" -> "5511999999999"
    """
    # Remove WhatsApp suffix
    phone = phone.replace("@s.whatsapp.net", "")
    phone = phone.replace("@c.us", "")

    # Keep only digits
    normalized = re.sub(r"\D", "", phone)

    return normalized


def is_valid_phone(phone: str) -> bool:
    """
    Validate phone number format.

    Expects Brazilian format: DDI + DDD + Number
    Example: 5511999999999 (13 digits)
    """
    normalized = normalize_phone(phone)

    # Brazilian phone: 55 + 2 digit DDD + 8-9 digit number = 12-13 digits
    if len(normalized) < 12 or len(normalized) > 13:
        return False

    # Must start with country code
    return normalized.startswith("55")


def is_phone_allowed(phone: str, allowed_phones: list[str]) -> bool:
    """Check if phone is in allowed list."""
    normalized = normalize_phone(phone)
    return any(normalize_phone(allowed) == normalized for allowed in allowed_phones)


def mask_phone(phone: str) -> str:
    """Mask phone number for safe logging."""
    normalized = normalize_phone(phone)
    if not normalized:
        return "***"
    if len(normalized) <= 6:
        return "*" * len(normalized)
    return f"{normalized[:4]}*****{normalized[-3:]}"


def sanitize_for_log(text: str | None, max_length: int = 80) -> str:
    """Sanitize and truncate text for logging without exposing full content."""
    cleaned = sanitize_text(text or "", max_length=max_length)
    if not cleaned:
        return "(empty)"
    if len(cleaned) < len((text or "").strip()):
        return f"{cleaned}..."
    return cleaned


def sanitize_text(text: str, max_length: int = 500) -> str:
    """Sanitize user input text."""
    if not text:
        return ""

    # Remove control characters
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)

    # Normalize whitespace
    cleaned = " ".join(cleaned.split())

    # Truncate
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned.strip()
