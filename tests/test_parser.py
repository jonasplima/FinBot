"""Tests for parser utilities."""

import pytest
from decimal import Decimal
from datetime import date

from app.utils.parser import (
    parse_amount,
    parse_date,
    parse_percentage,
    extract_month_year,
)


class TestParseAmount:
    """Tests for parse_amount function."""

    def test_simple_number(self):
        assert parse_amount("45") == Decimal("45.00")

    def test_with_reais(self):
        assert parse_amount("45 reais") == Decimal("45.00")

    def test_with_currency_symbol(self):
        assert parse_amount("R$ 100,50") == Decimal("100.50")

    def test_brazilian_format(self):
        assert parse_amount("1.500,00") == Decimal("1500.00")

    def test_us_format(self):
        assert parse_amount("1,500.00") == Decimal("1500.00")

    def test_decimal_comma(self):
        assert parse_amount("150,75") == Decimal("150.75")

    def test_empty_string(self):
        assert parse_amount("") is None

    def test_invalid_string(self):
        assert parse_amount("abc") is None


class TestParseDate:
    """Tests for parse_date function."""

    def test_hoje(self):
        today = date.today()
        assert parse_date("hoje") == today

    def test_ontem(self):
        today = date.today()
        expected = date(today.year, today.month, today.day - 1) if today.day > 1 else None
        result = parse_date("ontem")
        assert result is not None

    def test_day_month_format(self):
        result = parse_date("15/03")
        assert result is not None
        assert result.day == 15
        assert result.month == 3

    def test_full_date_format(self):
        result = parse_date("15/03/2024")
        assert result == date(2024, 3, 15)

    def test_month_name(self):
        result = parse_date("marco")
        assert result is not None
        assert result.month == 3


class TestParsePercentage:
    """Tests for parse_percentage function."""

    def test_with_percent_symbol(self):
        assert parse_percentage("50%") == Decimal("50.00")

    def test_with_por_cento(self):
        assert parse_percentage("50 por cento") == Decimal("50.00")

    def test_split_format(self):
        assert parse_percentage("50/50") == Decimal("50.00")

    def test_decimal_percentage(self):
        assert parse_percentage("33,33%") == Decimal("33.33")

    def test_empty_string(self):
        assert parse_percentage("") is None


class TestExtractMonthYear:
    """Tests for extract_month_year function."""

    def test_esse_mes(self):
        today = date.today()
        month, year = extract_month_year("esse mes")
        assert month == today.month
        assert year == today.year

    def test_month_name(self):
        month, year = extract_month_year("marco")
        assert month == 3

    def test_month_and_year(self):
        month, year = extract_month_year("marco 2024")
        assert month == 3
        assert year == 2024

    def test_february(self):
        month, year = extract_month_year("fevereiro")
        assert month == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
