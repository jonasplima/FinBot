"""Parsing utilities for amounts, dates and percentages."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta


def parse_amount(text: str) -> Decimal | None:
    """
    Parse monetary amount from text.

    Examples:
        "45 reais" -> 45.00
        "R$ 100,50" -> 100.50
        "150.75" -> 150.75
        "1.500,00" -> 1500.00
    """
    if not text:
        return None

    # Remove currency symbols and extra spaces
    cleaned = text.strip()
    cleaned = re.sub(r"[Rr]\$\s*", "", cleaned)
    cleaned = re.sub(r"\s*(reais?|real)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    # Handle Brazilian format (1.234,56) vs US format (1,234.56)
    if "," in cleaned and "." in cleaned:
        # Check which comes last
        if cleaned.rfind(",") > cleaned.rfind("."):
            # Brazilian format: 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: 1,234.56
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Only comma: assume decimal separator
        cleaned = cleaned.replace(",", ".")

    try:
        amount = Decimal(cleaned)
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date(text: str, reference: date | None = None) -> date | None:
    """
    Parse date from text.

    Examples:
        "hoje" -> today
        "ontem" -> yesterday
        "15/03" -> March 15 (current or next year)
        "15/03/2024" -> March 15, 2024
        "marco" -> first day of March
    """
    if not text:
        return None

    ref = reference or date.today()
    text_lower = text.strip().lower()

    # Relative dates
    if text_lower in ("hoje", "today"):
        return ref
    if text_lower in ("ontem", "yesterday"):
        return ref - relativedelta(days=1)
    if text_lower in ("anteontem",):
        return ref - relativedelta(days=2)

    # Month names
    months = {
        "janeiro": 1,
        "jan": 1,
        "fevereiro": 2,
        "fev": 2,
        "marco": 3,
        "mar": 3,
        "abril": 4,
        "abr": 4,
        "maio": 5,
        "mai": 5,
        "junho": 6,
        "jun": 6,
        "julho": 7,
        "jul": 7,
        "agosto": 8,
        "ago": 8,
        "setembro": 9,
        "set": 9,
        "outubro": 10,
        "out": 10,
        "novembro": 11,
        "nov": 11,
        "dezembro": 12,
        "dez": 12,
    }

    # Check if it's just a month name
    for month_name, month_num in months.items():
        if text_lower == month_name:
            year = ref.year if month_num <= ref.month else ref.year
            return date(year, month_num, 1)

    # Date patterns
    patterns = [
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", "%d/%m/%Y"),
        (r"(\d{1,2})/(\d{1,2})/(\d{2})", "%d/%m/%y"),
        (r"(\d{1,2})/(\d{1,2})", None),  # Day/Month only
        (r"(\d{1,2})-(\d{1,2})-(\d{4})", "%d-%m-%Y"),
    ]

    for pattern, fmt in patterns:
        match = re.match(pattern, text)
        if match:
            if fmt:
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    continue
            else:
                # Day/Month only
                day, month = int(match.group(1)), int(match.group(2))
                year = ref.year
                try:
                    result = date(year, month, day)
                    # If date is in the future, use previous year
                    if result > ref:
                        result = date(year - 1, month, day)
                    return result
                except ValueError:
                    continue

    return None


def parse_percentage(text: str) -> Decimal | None:
    """
    Parse percentage from text.

    Examples:
        "50%" -> 50.00
        "50 por cento" -> 50.00
        "50/50" -> 50.00 (first part)
        "70% meu" -> 70.00
    """
    if not text:
        return None

    text_lower = text.strip().lower()

    # Pattern: X% or X por cento
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|por\s*cento)", text_lower)
    if match:
        value = match.group(1).replace(",", ".")
        try:
            return Decimal(value).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None

    # Pattern: X/Y (split)
    match = re.search(r"(\d+)\s*/\s*(\d+)", text_lower)
    if match:
        first = Decimal(match.group(1))
        second = Decimal(match.group(2))
        total = first + second
        if total > 0:
            percentage = (first / total) * 100
            return percentage.quantize(Decimal("0.01"))

    return None


def extract_month_year(text: str, reference: date | None = None) -> tuple[int, int]:
    """
    Extract month and year from text for queries.

    Returns:
        (month, year) tuple
    """
    ref = reference or date.today()
    text_lower = text.strip().lower()

    # Month names
    months = {
        "janeiro": 1,
        "jan": 1,
        "fevereiro": 2,
        "fev": 2,
        "marco": 3,
        "mar": 3,
        "abril": 4,
        "abr": 4,
        "maio": 5,
        "mai": 5,
        "junho": 6,
        "jun": 6,
        "julho": 7,
        "jul": 7,
        "agosto": 8,
        "ago": 8,
        "setembro": 9,
        "set": 9,
        "outubro": 10,
        "out": 10,
        "novembro": 11,
        "nov": 11,
        "dezembro": 12,
        "dez": 12,
    }

    # Check for "esse mes" / "este mes"
    if any(phrase in text_lower for phrase in ("esse mes", "este mes", "mes atual")):
        return ref.month, ref.year

    # Find month name
    found_month = None
    for month_name, month_num in months.items():
        if month_name in text_lower:
            found_month = month_num
            break

    # Find year
    year_match = re.search(r"20\d{2}", text_lower)
    found_year = int(year_match.group()) if year_match else None

    # Defaults
    if found_month is None:
        found_month = ref.month
    if found_year is None:
        # If month is greater than current, assume previous year
        if found_month > ref.month:
            found_year = ref.year - 1
        else:
            found_year = ref.year

    return found_month, found_year
