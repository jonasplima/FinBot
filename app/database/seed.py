"""Seed database with initial categories and payment methods."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Category, PaymentMethod

logger = logging.getLogger(__name__)

# Categories defined by user
CATEGORIES = [
    # Negative (expenses)
    ("Alimentacao", "Negativo"),
    ("Assinatura", "Negativo"),
    ("Imprevistos", "Negativo"),
    ("Despesa Fixa", "Negativo"),
    ("Educacao", "Negativo"),
    ("Emprestimo", "Negativo"),
    ("Lazer", "Negativo"),
    ("Mercado", "Negativo"),
    ("Moradia", "Negativo"),
    ("Outros", "Negativo"),
    ("Parcelamento de Fatura", "Negativo"),
    ("Presente", "Negativo"),
    ("Saude e Beleza", "Negativo"),
    ("Servicos", "Negativo"),
    ("Transferencia", "Negativo"),
    ("Transporte", "Negativo"),
    ("Vestuario", "Negativo"),
    ("Viagem", "Negativo"),
    ("Reserva de Emergencia", "Negativo"),
    ("Investimento", "Negativo"),
    ("Categoria Nova 3", "Negativo"),
    # Positive (income)
    ("Salario - Adiantamento", "Positivo"),
    ("Salario", "Positivo"),
    ("Salario - 13o", "Positivo"),
    ("Reembolso - Aluguel + Condominio", "Positivo"),
    ("Bonus", "Positivo"),
    ("PLR", "Positivo"),
    ("VR (Flash)", "Positivo"),
    ("VR (Flash - Auxilio)", "Positivo"),
    ("Outros (entrada)", "Positivo"),
]

# Payment methods
PAYMENT_METHODS = [
    "Cartao de Credito",
    "Cartao de Debito",
    "Dinheiro",
    "VR",
    "Pix",
]


async def seed_categories(session: AsyncSession) -> None:
    """Seed categories if they don't exist."""
    result = await session.execute(select(Category).limit(1))
    if result.scalar_one_or_none() is not None:
        logger.info("Categories already seeded, skipping")
        return

    logger.info("Seeding categories...")
    for name, type_ in CATEGORIES:
        category = Category(name=name, type=type_)
        session.add(category)

    await session.commit()
    logger.info(f"Seeded {len(CATEGORIES)} categories")


async def seed_payment_methods(session: AsyncSession) -> None:
    """Seed payment methods if they don't exist."""
    result = await session.execute(select(PaymentMethod).limit(1))
    if result.scalar_one_or_none() is not None:
        logger.info("Payment methods already seeded, skipping")
        return

    logger.info("Seeding payment methods...")
    for name in PAYMENT_METHODS:
        method = PaymentMethod(name=name)
        session.add(method)

    await session.commit()
    logger.info(f"Seeded {len(PAYMENT_METHODS)} payment methods")


async def seed_all(session: AsyncSession) -> None:
    """Run all seed functions."""
    await seed_categories(session)
    await seed_payment_methods(session)
