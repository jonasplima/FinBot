"""Tests for ChartService."""

from decimal import Decimal

import pytest

from app.services.chart import ChartService


@pytest.fixture
def chart_service():
    """Create ChartService instance for testing."""
    return ChartService()


@pytest.fixture
def sample_category_data():
    """Sample data for pie chart (by category)."""
    return [
        {"category": "Alimentacao", "amount": Decimal("500.00")},
        {"category": "Transporte", "amount": Decimal("300.00")},
        {"category": "Lazer", "amount": Decimal("200.00")},
        {"category": "Mercado", "amount": Decimal("150.00")},
    ]


@pytest.fixture
def sample_expense_data():
    """Sample data for bar chart (top expenses)."""
    return [
        {"description": "Almoco restaurante", "amount": Decimal("150.00")},
        {"description": "Uber para o trabalho", "amount": Decimal("80.00")},
        {"description": "Cinema", "amount": Decimal("60.00")},
        {"description": "Supermercado", "amount": Decimal("45.00")},
        {"description": "Cafe", "amount": Decimal("25.00")},
    ]


@pytest.fixture
def sample_daily_data():
    """Sample data for line chart (daily totals)."""
    return [
        {"date": "01/04", "amount": Decimal("100.00")},
        {"date": "02/04", "amount": Decimal("50.00")},
        {"date": "05/04", "amount": Decimal("200.00")},
        {"date": "10/04", "amount": Decimal("75.00")},
        {"date": "15/04", "amount": Decimal("150.00")},
    ]


class TestChartService:
    """Tests for ChartService."""

    def test_generate_pie_chart_returns_bytes(self, chart_service, sample_category_data):
        """Test that pie chart generation returns PNG bytes."""
        result = chart_service.generate_pie_chart(sample_category_data)

        assert isinstance(result, bytes)
        assert len(result) > 0
        # Check PNG magic bytes
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_pie_chart_with_custom_title(self, chart_service, sample_category_data):
        """Test pie chart with custom title."""
        result = chart_service.generate_pie_chart(
            sample_category_data,
            title="Gastos de Abril/2024",
        )

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_pie_chart_empty_data(self, chart_service):
        """Test pie chart with empty data returns placeholder image."""
        result = chart_service.generate_pie_chart([])

        assert isinstance(result, bytes)
        assert len(result) > 0
        # Should still be a valid PNG
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_pie_chart_single_category(self, chart_service):
        """Test pie chart with single category."""
        data = [{"category": "Alimentacao", "amount": Decimal("500.00")}]
        result = chart_service.generate_pie_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_bar_chart_returns_bytes(self, chart_service, sample_expense_data):
        """Test that bar chart generation returns PNG bytes."""
        result = chart_service.generate_bar_chart(sample_expense_data)

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_bar_chart_with_custom_title(self, chart_service, sample_expense_data):
        """Test bar chart with custom title."""
        result = chart_service.generate_bar_chart(
            sample_expense_data,
            title="Top Gastos - Abril/2024",
        )

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_bar_chart_empty_data(self, chart_service):
        """Test bar chart with empty data returns placeholder image."""
        result = chart_service.generate_bar_chart([])

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_bar_chart_single_expense(self, chart_service):
        """Test bar chart with single expense."""
        data = [{"description": "Almoco", "amount": Decimal("50.00")}]
        result = chart_service.generate_bar_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_bar_chart_long_description_truncated(self, chart_service):
        """Test bar chart truncates long descriptions."""
        data = [
            {
                "description": "Este e um nome de despesa muito longo que deve ser truncado",
                "amount": Decimal("100.00"),
            }
        ]
        result = chart_service.generate_bar_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_line_chart_returns_bytes(self, chart_service, sample_daily_data):
        """Test that line chart generation returns PNG bytes."""
        result = chart_service.generate_line_chart(sample_daily_data)

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_line_chart_with_custom_title(self, chart_service, sample_daily_data):
        """Test line chart with custom title."""
        result = chart_service.generate_line_chart(
            sample_daily_data,
            title="Evolucao - Abril/2024",
        )

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_line_chart_empty_data(self, chart_service):
        """Test line chart with empty data returns placeholder image."""
        result = chart_service.generate_line_chart([])

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_generate_line_chart_single_day(self, chart_service):
        """Test line chart with single day."""
        data = [{"date": "01/04", "amount": Decimal("100.00")}]
        result = chart_service.generate_line_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_line_chart_many_days(self, chart_service):
        """Test line chart with many days (tests x-axis rotation)."""
        data = [
            {"date": f"{i:02d}/04", "amount": Decimal(str(i * 10))}
            for i in range(1, 25)
        ]
        result = chart_service.generate_line_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_to_float_converts_decimal(self, chart_service):
        """Test _to_float converts Decimal to float."""
        result = chart_service._to_float(Decimal("123.45"))
        assert result == 123.45
        assert isinstance(result, float)

    def test_to_float_handles_float(self, chart_service):
        """Test _to_float handles float input."""
        result = chart_service._to_float(123.45)
        assert result == 123.45
        assert isinstance(result, float)

    def test_to_float_handles_int(self, chart_service):
        """Test _to_float handles int input."""
        result = chart_service._to_float(100)
        assert result == 100.0
        assert isinstance(result, float)

    def test_truncate_text_short(self, chart_service):
        """Test _truncate_text with short text."""
        result = chart_service._truncate_text("Hello", 10)
        assert result == "Hello"

    def test_truncate_text_exact_length(self, chart_service):
        """Test _truncate_text with exact length text."""
        result = chart_service._truncate_text("HelloWorld", 10)
        assert result == "HelloWorld"

    def test_truncate_text_long(self, chart_service):
        """Test _truncate_text with long text."""
        result = chart_service._truncate_text("This is a very long text", 10)
        assert result == "This is..."
        assert len(result) == 10

    def test_pie_chart_handles_float_amounts(self, chart_service):
        """Test pie chart handles float amounts (not just Decimal)."""
        data = [
            {"category": "Alimentacao", "amount": 500.00},
            {"category": "Transporte", "amount": 300.00},
        ]
        result = chart_service.generate_pie_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_bar_chart_handles_float_amounts(self, chart_service):
        """Test bar chart handles float amounts."""
        data = [
            {"description": "Almoco", "amount": 50.00},
            {"description": "Jantar", "amount": 80.00},
        ]
        result = chart_service.generate_bar_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_line_chart_handles_float_amounts(self, chart_service):
        """Test line chart handles float amounts."""
        data = [
            {"date": "01/04", "amount": 100.00},
            {"date": "02/04", "amount": 50.00},
        ]
        result = chart_service.generate_line_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_pie_chart_many_categories(self, chart_service):
        """Test pie chart with many categories uses all colors."""
        data = [
            {"category": f"Categoria {i}", "amount": Decimal(str(100 - i * 5))}
            for i in range(12)
        ]
        result = chart_service.generate_pie_chart(data)

        assert isinstance(result, bytes)
        assert len(result) > 0
