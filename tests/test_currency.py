"""Tests for CurrencyService with Wise API integration and database fallback."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import ExchangeRate, Expense


class TestCurrencyService:
    """Tests for CurrencyService class."""

    @pytest.fixture
    def currency_service(self):
        """Create CurrencyService instance."""
        from app.services.currency import CurrencyService

        service = CurrencyService()
        service.clear_cache()
        return service

    def test_detect_currency_usd(self, currency_service):
        """Test detecting USD from text."""
        assert currency_service.detect_currency("gastei 50 dolares") == "USD"
        assert currency_service.detect_currency("comprei por 30 dollars") == "USD"
        assert currency_service.detect_currency("paguei em dolar") == "USD"

    def test_detect_currency_eur(self, currency_service):
        """Test detecting EUR from text."""
        assert currency_service.detect_currency("gastei 100 euros") == "EUR"
        assert currency_service.detect_currency("comprei por 50 euro") == "EUR"

    def test_detect_currency_gbp(self, currency_service):
        """Test detecting GBP from text."""
        assert currency_service.detect_currency("custou 30 libras") == "GBP"
        assert currency_service.detect_currency("paguei em libra esterlina") == "GBP"

    def test_detect_currency_krw(self, currency_service):
        """Test detecting KRW from text."""
        assert currency_service.detect_currency("gastei 10000 won") == "KRW"
        assert currency_service.detect_currency("custou 5000 won coreano") == "KRW"

    def test_detect_currency_huf(self, currency_service):
        """Test detecting HUF from text."""
        assert currency_service.detect_currency("paguei 2000 florim") == "HUF"
        assert currency_service.detect_currency("custou 1500 florim hungaro") == "HUF"

    def test_detect_currency_no_match(self, currency_service):
        """Test detecting no currency when none present."""
        assert currency_service.detect_currency("gastei 50 reais") is None
        assert currency_service.detect_currency("almoco no restaurante") is None

    @pytest.mark.anyio
    async def test_convert_to_brl_same_currency(self, currency_service):
        """Test conversion when currency is already BRL."""
        result = await currency_service.convert_to_brl(Decimal("100"), "BRL")

        assert result["success"] is True
        assert result["converted_amount"] == Decimal("100")
        assert result["exchange_rate"] == Decimal("1")

    @pytest.mark.anyio
    async def test_convert_to_brl_unsupported_currency(self, currency_service):
        """Test conversion with unsupported currency fails."""
        result = await currency_service.convert_to_brl(Decimal("100"), "XYZ")

        assert result["success"] is False
        assert "nao suportada" in result["error"]

    @pytest.mark.anyio
    async def test_convert_currency_same_currency(self, currency_service):
        """Test converting same currency returns same amount."""
        result = await currency_service.convert_currency(Decimal("100"), "USD", "USD")

        assert result["success"] is True
        assert result["converted_amount"] == Decimal("100")
        assert result["exchange_rate"] == Decimal("1")

    def test_format_conversion_result(self, currency_service):
        """Test formatting conversion result for display."""
        result = {
            "success": True,
            "original_amount": Decimal("100"),
            "original_currency": "USD",
            "converted_amount": Decimal("500"),
            "target_currency": "BRL",
            "exchange_rate": Decimal("5.0000"),
            "is_fallback": False,
        }

        formatted = currency_service.format_conversion_result(result)

        assert "USD 100.00" in formatted
        assert "BRL 500.00" in formatted
        assert "5.0000" in formatted

    def test_format_conversion_result_with_fallback(self, currency_service):
        """Test formatting shows fallback warning."""
        result = {
            "success": True,
            "original_amount": Decimal("100"),
            "original_currency": "USD",
            "converted_amount": Decimal("500"),
            "target_currency": "BRL",
            "exchange_rate": Decimal("5.0000"),
            "is_fallback": True,
        }

        formatted = currency_service.format_conversion_result(result)

        assert "cotacao aproximada" in formatted

    def test_format_conversion_result_with_wise_quote(self, currency_service):
        """Test formatting shows Wise real value."""
        result = {
            "success": True,
            "original_amount": Decimal("100"),
            "original_currency": "USD",
            "converted_amount": Decimal("500"),
            "target_currency": "BRL",
            "exchange_rate": Decimal("5.0000"),
            "is_fallback": False,
            "wise_real_value": Decimal("485.00"),
            "wise_effective_rate": Decimal("4.8500"),
        }

        formatted = currency_service.format_conversion_result(result)

        assert "Valor real Wise" in formatted
        assert "BRL 485.00" in formatted
        assert "4.8500" in formatted

    def test_format_conversion_result_error(self, currency_service):
        """Test formatting error result."""
        result = {
            "success": False,
            "error": "Moeda nao suportada",
        }

        formatted = currency_service.format_conversion_result(result)

        assert formatted == "Moeda nao suportada"

    def test_get_supported_currencies_list(self, currency_service):
        """Test getting formatted list of supported currencies."""
        currencies_list = currency_service.get_supported_currencies_list()

        assert "USD" in currencies_list
        assert "EUR" in currencies_list
        assert "GBP" in currencies_list
        assert "KRW" in currencies_list
        assert "HUF" in currencies_list
        assert "Dolar Americano" in currencies_list
        assert "Won Coreano" in currencies_list

    def test_cache_rate(self, currency_service):
        """Test that rates are cached."""
        currency_service._cache_rate("USD", Decimal("5.00"), "test")

        cached = currency_service._get_cached_rate("USD")
        assert cached == Decimal("5.00")

    def test_cache_valid(self, currency_service):
        """Test cache validity check."""
        currency_service._cache_rate("USD", Decimal("5.00"), "test")

        assert currency_service._is_cache_valid("USD") is True
        assert currency_service._is_cache_valid("EUR") is False

    def test_clear_cache(self, currency_service):
        """Test clearing cache."""
        currency_service._cache_rate("USD", Decimal("5.00"), "test")
        currency_service._cache_rate("EUR", Decimal("5.50"), "test")

        currency_service.clear_cache()

        assert currency_service._get_cached_rate("USD") is None
        assert currency_service._get_cached_rate("EUR") is None

    @pytest.mark.anyio
    async def test_get_wise_rate_success(self, currency_service):
        """Test fetching exchange rate from Wise API."""
        mock_response = [{"source": "USD", "target": "BRL", "rate": 5.25}]

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = AsyncMock(
                status_code=200,
                json=lambda: mock_response,
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            currency_service.wise_api_key = "test-wise-key"

            result = await currency_service._get_wise_rate("USD")

            assert result is not None
            assert result["rate"] == Decimal("5.25")
            assert result["source"] == "wise"

    @pytest.mark.anyio
    async def test_get_wise_quote_success(self, currency_service):
        """Test fetching quote from Wise API."""
        mock_response = {
            "sourceCurrency": "USD",
            "targetCurrency": "BRL",
            "sourceAmount": 100,
            "targetAmount": 485.50,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = AsyncMock(
                status_code=200,
                json=lambda: mock_response,
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            currency_service.wise_api_key = "test-wise-key"

            result = await currency_service._get_wise_quote(Decimal("100"), "USD")

            assert result is not None
            assert result["target_amount"] == Decimal("485.50")
            assert result["source_amount"] == Decimal("100")

    @pytest.mark.anyio
    async def test_get_exchange_rate_api_rate_success(self, currency_service):
        """Test fetching exchange rate from ExchangeRate API."""
        mock_response = {"result": "success", "conversion_rate": 5.15}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = AsyncMock(
                status_code=200,
                json=lambda: mock_response,
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            currency_service.exchange_rate_api_key = "test-api-key"

            result = await currency_service._get_exchange_rate_api_rate("USD")

            assert result is not None
            assert result["rate"] == Decimal("5.15")
            assert result["source"] == "exchangerate_api"

    @pytest.mark.anyio
    async def test_get_exchange_rate_priority(self, currency_service):
        """Test exchange rate fetching priority: Wise -> ExchangeRate -> Database."""
        # Mock Wise API success
        with patch.object(currency_service, "_get_wise_rate") as mock_wise:
            mock_wise.return_value = {"rate": Decimal("5.00"), "source": "wise"}

            result = await currency_service.get_exchange_rate("USD")

            assert result["success"] is True
            assert result["rate"] == Decimal("5.00")
            assert result["source"] == "wise"

    @pytest.mark.anyio
    async def test_get_exchange_rate_fallback_to_exchangerate_api(self, currency_service):
        """Test fallback to ExchangeRate API when Wise fails."""
        with patch.object(currency_service, "_get_wise_rate") as mock_wise:
            mock_wise.return_value = None

            with patch.object(currency_service, "_get_exchange_rate_api_rate") as mock_exchange:
                mock_exchange.return_value = {"rate": Decimal("5.10"), "source": "exchangerate_api"}

                result = await currency_service.get_exchange_rate("USD")

                assert result["success"] is True
                assert result["rate"] == Decimal("5.10")
                assert result["source"] == "exchangerate_api"

    @pytest.mark.anyio
    async def test_get_wise_real_value(self, currency_service):
        """Test getting Wise real value with fees."""
        with patch.object(currency_service, "_get_wise_quote") as mock_quote:
            mock_quote.return_value = {
                "target_amount": Decimal("485.00"),
                "source_amount": Decimal("100"),
                "effective_rate": Decimal("4.8500"),
                "source": "wise_quote",
            }

            result = await currency_service.get_wise_real_value(Decimal("100"), "USD")

            assert result["success"] is True
            assert result["target_amount"] == Decimal("485.00")
            assert result["effective_rate"] == Decimal("4.8500")

    @pytest.mark.anyio
    async def test_convert_to_brl_with_wise_quote(self, currency_service):
        """Test conversion includes Wise quote when requested."""
        with patch.object(currency_service, "get_exchange_rate") as mock_rate:
            mock_rate.return_value = {
                "success": True,
                "rate": Decimal("5.00"),
                "source": "wise",
            }

            with patch.object(currency_service, "get_wise_real_value") as mock_wise:
                mock_wise.return_value = {
                    "success": True,
                    "target_amount": Decimal("485.00"),
                    "effective_rate": Decimal("4.8500"),
                }

                result = await currency_service.convert_to_brl(
                    Decimal("100"), "USD", include_wise_quote=True
                )

                assert result["success"] is True
                assert result["converted_amount"] == Decimal("500.00")
                assert result["wise_real_value"] == Decimal("485.00")

    def test_should_update_db_rates_no_date(self, currency_service):
        """Test should update when no date provided."""
        assert currency_service._should_update_db_rates(None) is True

    def test_should_update_db_rates_old_date(self, currency_service):
        """Test should update when date is old."""
        old_date = datetime.now() - timedelta(days=10)
        assert currency_service._should_update_db_rates(old_date) is True

    def test_should_update_db_rates_recent_date(self, currency_service):
        """Test should not update when date is recent."""
        recent_date = datetime.now() - timedelta(days=1)
        assert currency_service._should_update_db_rates(recent_date) is False


class TestCurrencyServiceDatabase:
    """Tests for CurrencyService database operations."""

    @pytest.fixture
    def currency_service(self):
        """Create CurrencyService instance."""
        from app.services.currency import CurrencyService

        service = CurrencyService()
        service.clear_cache()
        service.wise_api_key = ""
        service.exchange_rate_api_key = ""
        return service

    @pytest.mark.anyio
    async def test_get_db_rate(self, currency_service, db_session):
        """Test getting rate from database."""
        # Insert test rate
        rate = ExchangeRate(
            currency_code="USD",
            rate_to_brl=Decimal("5.50"),
            source="test",
            updated_at=datetime.now(),
        )
        db_session.add(rate)
        await db_session.commit()

        result = await currency_service._get_db_rate(db_session, "USD")

        assert result is not None
        assert result["rate"] == Decimal("5.50")
        assert "test" in result["source"]

    @pytest.mark.anyio
    async def test_get_db_rate_not_found(self, currency_service, db_session):
        """Test getting rate that doesn't exist."""
        result = await currency_service._get_db_rate(db_session, "XYZ")

        assert result is None

    @pytest.mark.anyio
    async def test_save_db_rate_new(self, currency_service, db_session):
        """Test saving new rate to database."""
        await currency_service._save_db_rate(db_session, "EUR", Decimal("6.00"), "wise")

        result = await currency_service._get_db_rate(db_session, "EUR")

        assert result is not None
        assert result["rate"] == Decimal("6.00")

    @pytest.mark.anyio
    async def test_save_db_rate_update(self, currency_service, db_session):
        """Test updating existing rate in database."""
        # Insert initial rate
        rate = ExchangeRate(
            currency_code="GBP",
            rate_to_brl=Decimal("7.00"),
            source="old",
            updated_at=datetime.now() - timedelta(days=10),
        )
        db_session.add(rate)
        await db_session.commit()

        # Update rate
        await currency_service._save_db_rate(db_session, "GBP", Decimal("7.50"), "wise")

        result = await currency_service._get_db_rate(db_session, "GBP")

        assert result is not None
        assert result["rate"] == Decimal("7.50")


class TestCurrencyServiceIntegration:
    """Integration tests for currency conversion in expense flow."""

    @pytest.fixture
    def currency_service(self):
        """Create CurrencyService instance."""
        from app.services.currency import CurrencyService

        service = CurrencyService()
        service.clear_cache()
        return service

    @pytest.mark.anyio
    async def test_expense_with_currency_conversion(
        self, seeded_session, currency_service, test_phone
    ):
        """Test creating expense with currency conversion."""
        from app.services.expense import ExpenseService

        expense_service = ExpenseService()

        # Mock the exchange rate
        with patch.object(currency_service, "get_exchange_rate") as mock_rate:
            mock_rate.return_value = {
                "success": True,
                "rate": Decimal("5.00"),
                "source": "test",
            }

            # Simulate expense data with currency
            amount = Decimal("50")
            conversion = await currency_service.convert_to_brl(amount, "USD")

            expense_data = {
                "description": "Uber nos EUA",
                "amount": float(conversion["converted_amount"]),
                "category": "Transporte",
                "payment_method": "Pix",
                "original_currency": conversion["original_currency"],
                "original_amount": float(conversion["original_amount"]),
                "exchange_rate": float(conversion["exchange_rate"]),
            }

            result = await expense_service.create_expense(seeded_session, test_phone, expense_data)

            assert result["success"] is True

            # Verify expense was created with currency data
            from sqlalchemy import select

            expense = await seeded_session.execute(
                select(Expense).where(Expense.id == result["expense_id"])
            )
            expense = expense.scalar_one()

            assert expense.original_currency == "USD"
            assert expense.original_amount == Decimal("50")
            assert expense.exchange_rate is not None
