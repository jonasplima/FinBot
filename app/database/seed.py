"""Seed database with initial categories and payment methods."""

import logging
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Budget, Category, Expense, PaymentMethod

logger = logging.getLogger(__name__)

# Categories defined by user
CATEGORIES = [
    # Negative (expenses)
    ("Alimentação", "Negativo"),
    ("Assinatura", "Negativo"),
    ("Imprevistos", "Negativo"),
    ("Despesa Fixa", "Negativo"),
    ("Educação", "Negativo"),
    ("Emprestimo", "Negativo"),
    ("Lazer", "Negativo"),
    ("Mercado", "Negativo"),
    ("Moradia", "Negativo"),
    ("Outros", "Negativo"),
    ("Parcelamento de Fatura", "Negativo"),
    ("Presente", "Negativo"),
    ("Saúde e Beleza", "Negativo"),
    ("Servicos", "Negativo"),
    ("Transferencia", "Negativo"),
    ("Transporte", "Negativo"),
    ("Vestuario", "Negativo"),
    ("Viagem", "Negativo"),
    ("Reserva de Emergencia", "Negativo"),
    ("Investimento", "Negativo"),
    ("Metas", "Negativo"),
    # Positive (income)
    ("Salario - Adiantamento", "Positivo"),
    ("Salario", "Positivo"),
    ("Salario - 13o", "Positivo"),
    ("Reembolso", "Positivo"),
    ("Bonus", "Positivo"),
    ("PLR", "Positivo"),
    ("Vale Refeição", "Positivo"),
    ("Vale Alimentação", "Positivo"),
    ("Outros (entrada)", "Positivo"),
]

LEGACY_CATEGORY_MAPPINGS = {
    "Alimentacao": "Alimentação",
    "Educacao": "Educação",
    "Saude e Beleza": "Saúde e Beleza",
    "Reembolso - Aluguel + Condominio": "Reembolso",
    "VR (Flash)": "Vale Refeição",
    "VR (Flash - Auxilio)": "Vale Refeição",
    "Categoria Nova 3": "Outros",
}

# Payment methods
PAYMENT_METHODS = [
    "Cartão de Crédito",
    "Cartão de Débito",
    "Dinheiro",
    "Pix",
    "Vale Alimentação",
    "Vale Refeição",
]

LEGACY_PAYMENT_METHOD_MAPPINGS = {
    "Cartao de Credito": "Cartão de Crédito",
    "Cartao de Debito": "Cartão de Débito",
    "VR": "Vale Refeição",
}


async def seed_categories(session: AsyncSession) -> None:
    """Seed categories and reconcile legacy category names."""
    logger.info("Syncing categories...")

    result = await session.execute(select(Category))
    existing_categories: dict[str, Category] = {
        cast(str, category.name): category for category in result.scalars().all()
    }

    for legacy_name, target_name in LEGACY_CATEGORY_MAPPINGS.items():
        legacy_category = existing_categories.get(legacy_name)
        if not legacy_category:
            continue

        target_category = existing_categories.get(target_name)
        if target_category:
            await _reassign_category_references(session, legacy_category.id, target_category.id)
            await session.execute(delete(Category).where(Category.id == legacy_category.id))
            existing_categories.pop(legacy_name, None)
            continue

        legacy_category.name = target_name
        existing_categories[target_name] = legacy_category
        existing_categories.pop(legacy_name, None)

    expected_categories: dict[str, str] = dict(CATEGORIES)
    for name, type_ in expected_categories.items():
        category = existing_categories.get(name)
        if category:
            category.type = type_
            continue
        session.add(Category(name=name, type=type_))

    await session.commit()
    logger.info(f"Categories synced: {len(CATEGORIES)} active definitions")


async def _reassign_category_references(
    session: AsyncSession,
    old_category_id: int,
    new_category_id: int,
) -> None:
    """Move expense and budget references from one category to another."""
    await session.execute(
        update(Expense)
        .where(Expense.category_id == old_category_id)
        .values(category_id=new_category_id)
    )
    await session.execute(
        update(Budget)
        .where(Budget.category_id == old_category_id)
        .values(category_id=new_category_id)
    )


async def seed_payment_methods(session: AsyncSession) -> None:
    """Seed payment methods and reconcile legacy payment method names."""
    logger.info("Syncing payment methods...")

    result = await session.execute(select(PaymentMethod))
    existing_methods: dict[str, PaymentMethod] = {
        cast(str, method.name): method for method in result.scalars().all()
    }

    for legacy_name, target_name in LEGACY_PAYMENT_METHOD_MAPPINGS.items():
        legacy_method = existing_methods.get(legacy_name)
        if not legacy_method:
            continue

        target_method = existing_methods.get(target_name)
        if target_method:
            await session.execute(
                update(Expense)
                .where(Expense.payment_method_id == legacy_method.id)
                .values(payment_method_id=target_method.id)
            )
            await session.execute(delete(PaymentMethod).where(PaymentMethod.id == legacy_method.id))
            existing_methods.pop(legacy_name, None)
            continue

        legacy_method.name = target_name
        existing_methods[target_name] = legacy_method
        existing_methods.pop(legacy_name, None)

    for name in PAYMENT_METHODS:
        if name in existing_methods:
            continue
        session.add(PaymentMethod(name=name))

    await session.commit()
    logger.info(f"Payment methods synced: {len(PAYMENT_METHODS)} active definitions")


async def seed_all(session: AsyncSession) -> None:
    """Run all seed functions."""
    await seed_categories(session)
    await seed_payment_methods(session)
