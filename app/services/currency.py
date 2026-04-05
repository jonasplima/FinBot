"""Currency conversion service with Wise API, ExchangeRate API fallback, and database-based rates."""

import logging
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import ExchangeRate

logger = logging.getLogger(__name__)
settings = get_settings()

# Supported currencies with their common names in Portuguese
SUPPORTED_CURRENCIES = {
    "USD": {
        "name": "Dolar Americano",
        "symbols": ["dolar", "dolares", "dollar", "dollars", "usd", "$"],
    },
    "EUR": {"name": "Euro", "symbols": ["euro", "euros", "eur"]},
    "GBP": {
        "name": "Libra Esterlina",
        "symbols": [
            "libra",
            "libras",
            "libra esterlina",
            "libras esterlinas",
            "gbp",
            "pound",
            "pounds",
        ],
    },
    "ARS": {"name": "Peso Argentino", "symbols": ["peso argentino", "pesos argentinos", "ars"]},
    "JPY": {"name": "Iene", "symbols": ["iene", "ienes", "yen", "jpy"]},
    "CAD": {"name": "Dolar Canadense", "symbols": ["dolar canadense", "cad"]},
    "AUD": {"name": "Dolar Australiano", "symbols": ["dolar australiano", "aud"]},
    "CHF": {"name": "Franco Suico", "symbols": ["franco suico", "francos suicos", "chf"]},
    "CNY": {"name": "Yuan", "symbols": ["yuan", "yuans", "cny", "renminbi"]},
    "MXN": {"name": "Peso Mexicano", "symbols": ["peso mexicano", "pesos mexicanos", "mxn"]},
    "KRW": {"name": "Won Coreano", "symbols": ["won", "wons", "won coreano", "krw"]},
    "HUF": {"name": "Florim Hungaro", "symbols": ["florim", "florins", "florim hungaro", "huf"]},
}


class CurrencyService:
    """
    Service for currency conversion with multi-tier fallback.

    Priority order:
    1. Wise API (GET /v1/rates for commercial, POST /v3/quotes for real value)
    2. ExchangeRate API (fallback)
    3. Database-stored rates (last resort, updated weekly)

    Can be used for:
    1. Converting expense amounts from foreign currency to BRL
    2. Standalone currency conversion queries (e.g., "quanto e 100 dolares em reais")
    """

    # Class-level cache for exchange rates
    _rate_cache: dict[str, dict] = {}

    def __init__(self):
        # Wise API (primary)
        self.wise_api_url = settings.wise_api_url
        self.wise_api_key = settings.wise_api_key

        # ExchangeRate API (fallback)
        self.exchange_rate_api_url = settings.exchange_rate_api_url
        self.exchange_rate_api_key = settings.exchange_rate_api_key

        # Cache settings
        self.cache_ttl = settings.exchange_rate_cache_ttl
        self.fallback_rates_update_days = settings.fallback_rates_update_days

    def _is_cache_valid(self, currency: str) -> bool:
        """Check if cached rate is still valid."""
        if currency not in self._rate_cache:
            return False

        cached = self._rate_cache[currency]
        cache_time = cached.get("timestamp")

        if not cache_time:
            return False

        elapsed = (datetime.now() - cache_time).total_seconds()
        return elapsed < self.cache_ttl

    def _get_cached_rate(self, from_currency: str) -> Decimal | None:
        """Get cached exchange rate to BRL."""
        if not self._is_cache_valid(from_currency):
            return None

        return self._rate_cache[from_currency].get("rate_to_brl")

    def _cache_rate(self, from_currency: str, rate: Decimal, source: str = "api") -> None:
        """Cache exchange rate."""
        self._rate_cache[from_currency] = {
            "rate_to_brl": rate,
            "timestamp": datetime.now(),
            "source": source,
        }
        logger.info(f"Cached exchange rate from {source}: 1 {from_currency} = {rate} BRL")

    async def _get_wise_rate(self, from_currency: str) -> dict | None:
        """
        Get exchange rate from Wise API (commercial/mid-market rate).

        Uses GET /v1/rates endpoint.
        """
        if not self.wise_api_key:
            logger.debug("Wise API key not configured, skipping")
            return None

        try:
            url = f"{self.wise_api_url}/v1/rates"
            params = {"source": from_currency, "target": "BRL"}
            headers = {"Authorization": f"Bearer {self.wise_api_key}"}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params, headers=headers)

                if response.status_code != 200:
                    logger.warning(f"Wise rates API error: {response.status_code}")
                    return None

                data = response.json()

                if not data or not isinstance(data, list) or len(data) == 0:
                    logger.warning(f"Wise rates API empty response: {data}")
                    return None

                rate = Decimal(str(data[0].get("rate", 0)))

                if rate <= 0:
                    return None

                logger.info(f"Wise commercial rate: 1 {from_currency} = {rate} BRL")
                return {"rate": rate, "source": "wise"}

        except httpx.TimeoutException:
            logger.warning(f"Wise rates API timeout for {from_currency}")
            return None
        except Exception as e:
            logger.warning(f"Wise rates API error: {e}")
            return None

    async def _get_wise_quote(self, amount: Decimal, from_currency: str) -> dict | None:
        """
        Get real conversion value from Wise API (with fees and IOF).

        Uses POST /v3/quotes endpoint (unauthenticated).
        Returns the actual amount that arrives after Wise deducts fees.
        """
        try:
            url = f"{self.wise_api_url}/v3/quotes"
            headers = {"Content-Type": "application/json"}

            # If we have API key, use authenticated endpoint
            if self.wise_api_key:
                headers["Authorization"] = f"Bearer {self.wise_api_key}"

            payload = {
                "sourceCurrency": from_currency,
                "targetCurrency": "BRL",
                "sourceAmount": float(amount),
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    logger.warning(f"Wise quotes API error: {response.status_code}")
                    return None

                data = response.json()

                # Extract target amount (what actually arrives)
                target_amount = data.get("targetAmount")
                source_amount = data.get("sourceAmount", float(amount))

                if not target_amount or target_amount <= 0:
                    logger.warning(f"Wise quotes API invalid response: {data}")
                    return None

                target_amount = Decimal(str(target_amount))
                source_amount = Decimal(str(source_amount))
                effective_rate = (target_amount / source_amount).quantize(Decimal("0.0001"))

                logger.info(
                    f"Wise quote: {from_currency} {source_amount} -> BRL {target_amount} "
                    f"(effective rate: {effective_rate})"
                )

                return {
                    "target_amount": target_amount,
                    "source_amount": source_amount,
                    "effective_rate": effective_rate,
                    "source": "wise_quote",
                }

        except httpx.TimeoutException:
            logger.warning(f"Wise quotes API timeout for {from_currency}")
            return None
        except Exception as e:
            logger.warning(f"Wise quotes API error: {e}")
            return None

    async def _get_exchange_rate_api_rate(self, from_currency: str) -> dict | None:
        """Get exchange rate from ExchangeRate API (fallback)."""
        if not self.exchange_rate_api_key:
            logger.debug("ExchangeRate API key not configured, skipping")
            return None

        try:
            url = f"{self.exchange_rate_api_url}/{self.exchange_rate_api_key}/pair/{from_currency}/BRL"

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)

                if response.status_code != 200:
                    logger.warning(f"ExchangeRate API error: {response.status_code}")
                    return None

                data = response.json()

                if data.get("result") != "success":
                    logger.warning(f"ExchangeRate API failed: {data}")
                    return None

                rate = Decimal(str(data.get("conversion_rate", 0)))

                if rate <= 0:
                    return None

                logger.info(f"ExchangeRate API rate: 1 {from_currency} = {rate} BRL")
                return {"rate": rate, "source": "exchangerate_api"}

        except httpx.TimeoutException:
            logger.warning(f"ExchangeRate API timeout for {from_currency}")
            return None
        except Exception as e:
            logger.warning(f"ExchangeRate API error: {e}")
            return None

    async def _get_db_rate(self, session: AsyncSession, from_currency: str) -> dict | None:
        """Get exchange rate from database."""
        try:
            result = await session.execute(
                select(ExchangeRate).where(ExchangeRate.currency_code == from_currency)
            )
            rate_record = result.scalar_one_or_none()

            if rate_record:
                logger.info(f"Database rate for {from_currency}: {rate_record.rate_to_brl}")
                return {
                    "rate": rate_record.rate_to_brl,
                    "source": f"db_{rate_record.source}",
                    "updated_at": rate_record.updated_at,
                }

            return None

        except Exception as e:
            logger.error(f"Error fetching rate from database: {e}")
            return None

    async def _save_db_rate(
        self, session: AsyncSession, currency: str, rate: Decimal, source: str
    ) -> None:
        """Save or update exchange rate in database."""
        try:
            result = await session.execute(
                select(ExchangeRate).where(ExchangeRate.currency_code == currency)
            )
            rate_record = result.scalar_one_or_none()

            if rate_record:
                rate_record.rate_to_brl = rate
                rate_record.source = source
                rate_record.updated_at = datetime.now()
            else:
                rate_record = ExchangeRate(
                    currency_code=currency,
                    rate_to_brl=rate,
                    source=source,
                    updated_at=datetime.now(),
                )
                session.add(rate_record)

            await session.commit()
            logger.info(f"Saved rate to database: {currency} = {rate} BRL (source: {source})")

        except Exception as e:
            logger.error(f"Error saving rate to database: {e}")
            await session.rollback()

    def _should_update_db_rates(self, updated_at: datetime | None) -> bool:
        """Check if database rates should be updated."""
        if not updated_at:
            return True

        days_elapsed = (datetime.now() - updated_at).days
        return days_elapsed >= self.fallback_rates_update_days

    async def update_fallback_rates(self) -> bool:
        """
        Update fallback rates in database from API.

        Should be called periodically (e.g., weekly via scheduler).
        """
        logger.info("Updating fallback rates in database...")
        updated_count = 0

        async with async_session() as session:
            for currency in SUPPORTED_CURRENCIES:
                rate = None
                source = None

                # Try Wise first
                result = await self._get_wise_rate(currency)
                if result:
                    rate = result["rate"]
                    source = "wise"
                else:
                    # Try ExchangeRate API
                    result = await self._get_exchange_rate_api_rate(currency)
                    if result:
                        rate = result["rate"]
                        source = "exchangerate_api"

                if rate and source:
                    await self._save_db_rate(session, currency, rate, source)
                    updated_count += 1
                else:
                    logger.warning(f"Could not update fallback rate for {currency}")

        logger.info(f"Updated {updated_count}/{len(SUPPORTED_CURRENCIES)} fallback rates")
        return updated_count > 0

    async def _get_fallback_rate(self, from_currency: str) -> dict:
        """Get fallback exchange rate from database."""
        async with async_session() as session:
            db_rate = await self._get_db_rate(session, from_currency)

            if db_rate:
                # Check if we should update
                if self._should_update_db_rates(db_rate.get("updated_at")):
                    logger.info("Database rates are old, triggering update...")
                    await self.update_fallback_rates()
                    # Fetch again after update
                    db_rate = await self._get_db_rate(session, from_currency)

                if db_rate:
                    logger.warning(
                        f"Using database fallback rate for {from_currency}: {db_rate['rate']}"
                    )
                    return {
                        "success": True,
                        "rate": db_rate["rate"],
                        "is_fallback": True,
                        "source": db_rate["source"],
                    }

        return {"success": False, "error": f"Moeda {from_currency} nao suportada"}

    async def get_exchange_rate(self, from_currency: str) -> dict:
        """
        Get exchange rate from currency to BRL.

        Priority: Wise API -> ExchangeRate API -> Database fallback

        Args:
            from_currency: ISO currency code (USD, EUR, etc.)

        Returns:
            Dict with success status and rate or error message
        """
        from_currency = from_currency.upper()

        if from_currency == "BRL":
            return {"success": True, "rate": Decimal("1")}

        if from_currency not in SUPPORTED_CURRENCIES:
            return {"success": False, "error": f"Moeda {from_currency} nao suportada"}

        # Check cache first
        cached_rate = self._get_cached_rate(from_currency)
        if cached_rate is not None:
            logger.debug(f"Using cached rate for {from_currency}: {cached_rate}")
            return {"success": True, "rate": cached_rate}

        # Try Wise API first
        result = await self._get_wise_rate(from_currency)
        if result:
            self._cache_rate(from_currency, result["rate"], "wise")
            return {"success": True, "rate": result["rate"], "source": "wise"}

        # Try ExchangeRate API
        result = await self._get_exchange_rate_api_rate(from_currency)
        if result:
            self._cache_rate(from_currency, result["rate"], "exchangerate_api")
            return {"success": True, "rate": result["rate"], "source": "exchangerate_api"}

        # Use database fallback
        return await self._get_fallback_rate(from_currency)

    async def get_wise_real_value(self, amount: Decimal, from_currency: str) -> dict:
        """
        Get real conversion value from Wise (after IOF and fees).

        This shows what actually arrives in BRL after Wise deducts:
        - IOF (tax)
        - Wise service fee

        Args:
            amount: Amount in foreign currency
            from_currency: ISO currency code

        Returns:
            Dict with quote details or None if unavailable
        """
        from_currency = from_currency.upper()

        if from_currency == "BRL":
            return {
                "success": True,
                "source_amount": amount,
                "target_amount": amount,
                "effective_rate": Decimal("1"),
                "source": "same_currency",
            }

        quote = await self._get_wise_quote(amount, from_currency)

        if quote:
            return {
                "success": True,
                "source_amount": quote["source_amount"],
                "target_amount": quote["target_amount"],
                "effective_rate": quote["effective_rate"],
                "source": "wise_quote",
            }

        return {"success": False, "error": "Cotacao Wise indisponivel"}

    async def convert_to_brl(
        self,
        amount: Decimal,
        from_currency: str,
        include_wise_quote: bool = False,
    ) -> dict:
        """
        Convert amount from foreign currency to BRL.

        Args:
            amount: Amount in foreign currency
            from_currency: ISO currency code (USD, EUR, etc.)
            include_wise_quote: If True, also fetch Wise real value with fees

        Returns:
            Dict with success status, converted amount, and rate used
        """
        from_currency = from_currency.upper()

        if from_currency == "BRL":
            return {
                "success": True,
                "original_amount": amount,
                "original_currency": "BRL",
                "converted_amount": amount,
                "exchange_rate": Decimal("1"),
            }

        rate_result = await self.get_exchange_rate(from_currency)

        if not rate_result["success"]:
            return rate_result

        rate = rate_result["rate"]
        converted_amount = (amount * rate).quantize(Decimal("0.01"))

        result = {
            "success": True,
            "original_amount": amount,
            "original_currency": from_currency,
            "converted_amount": converted_amount,
            "exchange_rate": rate,
            "is_fallback": rate_result.get("is_fallback", False),
            "source": rate_result.get("source", "unknown"),
        }

        # Optionally include Wise quote (real value with fees)
        if include_wise_quote:
            wise_quote = await self.get_wise_real_value(amount, from_currency)
            if wise_quote.get("success"):
                result["wise_real_value"] = wise_quote["target_amount"]
                result["wise_effective_rate"] = wise_quote["effective_rate"]

        return result

    async def convert_currency(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str = "BRL",
    ) -> dict:
        """
        Convert amount between any two supported currencies.

        This method can be used for standalone conversion queries
        without registering an expense.

        Args:
            amount: Amount to convert
            from_currency: Source currency ISO code
            to_currency: Target currency ISO code (default: BRL)

        Returns:
            Dict with conversion details for user display
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        # Validate currencies
        if from_currency not in SUPPORTED_CURRENCIES and from_currency != "BRL":
            return {"success": False, "error": f"Moeda de origem {from_currency} nao suportada"}

        if to_currency not in SUPPORTED_CURRENCIES and to_currency != "BRL":
            return {"success": False, "error": f"Moeda de destino {to_currency} nao suportada"}

        # Same currency
        if from_currency == to_currency:
            return {
                "success": True,
                "original_amount": amount,
                "original_currency": from_currency,
                "converted_amount": amount,
                "target_currency": to_currency,
                "exchange_rate": Decimal("1"),
            }

        # Convert to BRL first, then to target if needed
        if to_currency == "BRL":
            result = await self.convert_to_brl(amount, from_currency, include_wise_quote=True)
            if result["success"]:
                result["target_currency"] = "BRL"
            return result

        # Convert from_currency -> BRL -> to_currency
        to_brl = await self.convert_to_brl(amount, from_currency)
        if not to_brl["success"]:
            return to_brl

        brl_amount = to_brl["converted_amount"]

        # Get rate from target currency to BRL (inverse)
        target_rate_result = await self.get_exchange_rate(to_currency)
        if not target_rate_result["success"]:
            return target_rate_result

        target_rate = target_rate_result["rate"]
        final_amount = (brl_amount / target_rate).quantize(Decimal("0.01"))

        return {
            "success": True,
            "original_amount": amount,
            "original_currency": from_currency,
            "converted_amount": final_amount,
            "target_currency": to_currency,
            "exchange_rate": (final_amount / amount).quantize(Decimal("0.0001"))
            if amount > 0
            else Decimal("0"),
            "is_fallback": to_brl.get("is_fallback", False)
            or target_rate_result.get("is_fallback", False),
        }

    def detect_currency(self, text: str) -> str | None:
        """
        Detect currency from text message.

        Args:
            text: User message text

        Returns:
            ISO currency code if detected, None otherwise
        """
        text_lower = text.lower()

        for code, info in SUPPORTED_CURRENCIES.items():
            for symbol in info["symbols"]:
                if symbol in text_lower:
                    return code

        return None

    def format_conversion_result(self, result: dict) -> str:
        """
        Format conversion result for user display.

        Args:
            result: Conversion result dict

        Returns:
            Formatted message string
        """
        if not result["success"]:
            return result.get("error", "Erro na conversao")

        original = result["original_amount"]
        original_currency = result["original_currency"]
        converted = result["converted_amount"]
        target_currency = result.get("target_currency", "BRL")
        rate = result["exchange_rate"]

        # Get currency names
        from_name = SUPPORTED_CURRENCIES.get(original_currency, {}).get("name", original_currency)
        to_name = SUPPORTED_CURRENCIES.get(target_currency, {}).get("name", target_currency)
        if target_currency == "BRL":
            to_name = "Real"

        msg = f"{original_currency} {original:.2f} ({from_name})\n"
        msg += f"= {target_currency} {converted:.2f} ({to_name})\n\n"
        msg += f"Cotacao comercial: 1 {original_currency} = {rate:.4f} {target_currency}"

        # Show Wise real value if available
        if result.get("wise_real_value"):
            wise_value = result["wise_real_value"]
            wise_rate = result.get("wise_effective_rate", Decimal("0"))
            msg += "\n\nValor real Wise (com IOF e taxas):\n"
            msg += f"= BRL {wise_value:.2f}\n"
            msg += f"Taxa efetiva: 1 {original_currency} = {wise_rate:.4f} BRL"

        if result.get("is_fallback"):
            msg += "\n\n(cotacao aproximada)"

        return msg

    def get_supported_currencies_list(self) -> str:
        """Get formatted list of supported currencies."""
        msg = "Moedas suportadas para conversao:\n\n"

        for code, info in SUPPORTED_CURRENCIES.items():
            msg += f"  {code} - {info['name']}\n"

        msg += "\nExemplos:\n"
        msg += "- 'quanto e 100 dolares em reais'\n"
        msg += "- 'converter 50 euros'\n"
        msg += "- 'gastei 30 dolares no uber'"

        return msg

    def clear_cache(self) -> None:
        """Clear all cached exchange rates."""
        self._rate_cache.clear()
        logger.info("Exchange rate cache cleared")
