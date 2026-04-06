"""Shared test fixtures for FinBot tests."""

import os

# Set test environment variables BEFORE importing app modules
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ["EVOLUTION_API_URL"] = "http://localhost:8080"
os.environ["EVOLUTION_API_KEY"] = "test-key"
os.environ["EVOLUTION_INSTANCE"] = "test-instance"
os.environ["OWNER_PHONE"] = "5511999999999"
os.environ["GEMINI_API_KEY"] = "test-gemini-key"
os.environ["ADMIN_SECRET"] = "test-secret"

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

# Create a separate Base for testing
TestBase = declarative_base()

# Now we can safely import models (they use their own Base)
# We need to recreate the models for testing or import them carefully
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship


class Category(TestBase):
    """Test Category model."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    type = Column(String(10), nullable=False)
    expenses = relationship("Expense", back_populates="category")
    budgets = relationship("Budget", back_populates="category")


class PaymentMethod(TestBase):
    """Test PaymentMethod model."""

    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)
    expenses = relationship("Expense", back_populates="payment_method")


class User(TestBase):
    """Test User model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=True)
    display_name = Column(String(120), nullable=True)
    email = Column(String(255), nullable=True)
    accepted_terms = Column(Boolean, default=False, nullable=False)
    accepted_terms_at = Column(DateTime, nullable=True)
    terms_version = Column(String(30), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    preferred_channel = Column(String(30), default="whatsapp", nullable=False)
    timezone = Column(String(50), nullable=True)
    web_access_enabled = Column(Boolean, default=False, nullable=False)
    limits_enabled = Column(Boolean, default=True, nullable=False)
    daily_text_limit = Column(Integer, default=100, nullable=False)
    daily_media_limit = Column(Integer, default=20, nullable=False)
    daily_ai_limit = Column(Integer, default=50, nullable=False)
    notification_preferences = Column(JSON, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True)


class Expense(TestBase):
    """Test Expense model."""

    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"), nullable=False)
    type = Column(String(10), nullable=False)
    installment_current = Column(Integer, nullable=True)
    installment_total = Column(Integer, nullable=True)
    is_shared = Column(Boolean, default=False, nullable=False)
    shared_percentage = Column(Numeric(5, 2), nullable=True)
    original_currency = Column(String(3), nullable=True)
    original_amount = Column(Numeric(12, 2), nullable=True)
    exchange_rate = Column(Numeric(12, 6), nullable=True)
    is_recurring = Column(Boolean, default=False, nullable=False)
    recurring_day = Column(Integer, nullable=True)
    recurring_active = Column(Boolean, default=True, nullable=True)
    date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    category = relationship("Category", back_populates="expenses")
    payment_method = relationship("PaymentMethod", back_populates="expenses")

    @property
    def installment_display(self):
        if self.installment_current and self.installment_total:
            return f"{self.installment_current}/{self.installment_total}"
        return None


class PendingConfirmation(TestBase):
    """Test PendingConfirmation model."""

    __tablename__ = "pending_confirmations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    data = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Budget(TestBase):
    """Test Budget model."""

    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    monthly_limit = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True)

    category = relationship("Category", back_populates="budgets")
    alerts = relationship("BudgetAlert", back_populates="budget", cascade="all, delete-orphan")


class BudgetAlert(TestBase):
    """Test BudgetAlert model."""

    __tablename__ = "budget_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey("budgets.id"), nullable=False)
    threshold_percent = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    sent_at = Column(DateTime, nullable=False, default=datetime.now)

    budget = relationship("Budget", back_populates="alerts")


class Goal(TestBase):
    """Test Goal model."""

    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    description = Column(String(200), nullable=False)
    target_amount = Column(Numeric(12, 2), nullable=False)
    current_amount = Column(Numeric(12, 2), default=0, nullable=False)
    deadline = Column(Date, nullable=False)
    start_date = Column(Date, nullable=False, default=date.today)
    is_active = Column(Boolean, default=True, nullable=False)
    is_achieved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True)

    updates = relationship("GoalUpdate", back_populates="goal", cascade="all, delete-orphan")


class GoalUpdate(TestBase):
    """Test GoalUpdate model."""

    __tablename__ = "goal_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    previous_amount = Column(Numeric(12, 2), nullable=False)
    new_amount = Column(Numeric(12, 2), nullable=False)
    update_type = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    goal = relationship("Goal", back_populates="updates")


class ExchangeRate(TestBase):
    """Test ExchangeRate model."""

    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    currency_code = Column(String(3), nullable=False, unique=True)
    rate_to_brl = Column(Numeric(12, 6), nullable=False)
    source = Column(String(30), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


@pytest.fixture
def anyio_backend():
    """Use asyncio for async tests."""
    return "asyncio"


@pytest.fixture
async def async_engine():
    """Create async SQLite engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def db_session(async_engine):
    """Create async database session for testing."""
    async_session_factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with async_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def seeded_session(db_session):
    """Database session with seeded categories and payment methods."""
    categories = [
        Category(name="Alimentação", type="Negativo"),
        Category(name="Transporte", type="Negativo"),
        Category(name="Lazer", type="Negativo"),
        Category(name="Mercado", type="Negativo"),
        Category(name="Assinatura", type="Negativo"),
        Category(name="Vestuario", type="Negativo"),
        Category(name="Outros", type="Negativo"),
        Category(name="Salario", type="Positivo"),
        Category(name="Bonus", type="Positivo"),
    ]

    payment_methods = [
        PaymentMethod(name="Pix"),
        PaymentMethod(name="Cartão de Crédito"),
        PaymentMethod(name="Cartão de Débito"),
        PaymentMethod(name="Dinheiro"),
        PaymentMethod(name="Vale Alimentação"),
        PaymentMethod(name="Vale Refeição"),
    ]

    for cat in categories:
        db_session.add(cat)
    for pm in payment_methods:
        db_session.add(pm)

    await db_session.commit()

    yield db_session


@pytest.fixture
def sample_expense_data():
    """Sample expense data for testing."""
    return {
        "description": "Almoco no restaurante",
        "amount": 45.50,
        "category": "Alimentação",
        "payment_method": "Pix",
        "installments": None,
        "is_shared": False,
        "shared_percentage": None,
        "is_recurring": False,
        "recurring_day": None,
    }


@pytest.fixture
def sample_installment_data():
    """Sample installment expense data for testing."""
    return {
        "description": "Tenis Nike",
        "amount": 300.00,
        "category": "Vestuario",
        "payment_method": "Cartão de Crédito",
        "installments": 3,
        "is_shared": False,
        "shared_percentage": None,
        "is_recurring": False,
        "recurring_day": None,
    }


@pytest.fixture
def sample_recurring_data():
    """Sample recurring expense data for testing."""
    return {
        "description": "Netflix",
        "amount": 55.90,
        "category": "Assinatura",
        "payment_method": "Cartão de Crédito",
        "installments": None,
        "is_shared": False,
        "shared_percentage": None,
        "is_recurring": True,
        "recurring_day": 15,
    }


@pytest.fixture
def sample_shared_data():
    """Sample shared expense data for testing."""
    return {
        "description": "Mercado da semana",
        "amount": 200.00,
        "category": "Mercado",
        "payment_method": "Pix",
        "installments": None,
        "is_shared": True,
        "shared_percentage": 60.0,
        "is_recurring": False,
        "recurring_day": None,
    }


@pytest.fixture
def test_phone():
    """Test phone number."""
    return "5511999999999"


@pytest.fixture
def mock_gemini_service():
    """Mock GeminiService for testing without API calls."""
    with patch("app.services.gemini.GeminiService") as MockGemini:
        mock_instance = MagicMock()
        mock_instance.process_message = AsyncMock()
        mock_instance.process_image = AsyncMock()
        mock_instance.process_pdf_text = AsyncMock()
        mock_instance.evaluate_confirmation_response = AsyncMock()
        MockGemini.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_evolution_service():
    """Mock EvolutionService for testing without API calls."""
    with patch("app.services.evolution.EvolutionService") as MockEvolution:
        mock_instance = MagicMock()
        mock_instance.send_text = AsyncMock()
        mock_instance.send_document = AsyncMock()
        mock_instance.download_media = AsyncMock()
        mock_instance.extract_message_data = MagicMock()
        MockEvolution.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_settings():
    """Mock application settings for testing."""
    with patch("app.config.get_settings") as mock_get:
        mock_settings = MagicMock()
        mock_settings.database_url = "sqlite+aiosqlite:///:memory:"
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.evolution_api_url = "http://localhost:8080"
        mock_settings.evolution_api_key = "test-key"
        mock_settings.evolution_instance = "test-instance"
        mock_settings.owner_phone = "5511999999999"
        mock_settings.allowed_phones = []
        mock_settings.gemini_api_key = "test-gemini-key"
        mock_settings.admin_secret = "test-secret"
        mock_settings.webhook_secret = "test-webhook-secret"
        mock_settings.terms_version = "2026-04"
        mock_settings.default_daily_text_limit = 100
        mock_settings.default_daily_media_limit = 20
        mock_settings.default_daily_ai_limit = 50
        mock_settings.webhook_idempotency_ttl_seconds = 172800
        mock_settings.user_limit_defaults.return_value = {
            "daily_text_limit": 100,
            "daily_media_limit": 20,
            "daily_ai_limit": 50,
        }
        mock_get.return_value = mock_settings
        yield mock_settings


@pytest.fixture
async def expense_in_db(seeded_session, test_phone):
    """Create an expense in the database for testing."""
    from sqlalchemy import select

    cat_result = await seeded_session.execute(
        select(Category).where(Category.name == "Alimentação")
    )
    category = cat_result.scalar_one()

    pm_result = await seeded_session.execute(
        select(PaymentMethod).where(PaymentMethod.name == "Pix")
    )
    payment_method = pm_result.scalar_one()

    expense = Expense(
        user_phone=test_phone,
        description="Teste expense",
        amount=Decimal("50.00"),
        category_id=category.id,
        payment_method_id=payment_method.id,
        type="Negativo",
        date=date.today(),
        created_at=datetime.now(),
    )

    seeded_session.add(expense)
    await seeded_session.commit()
    await seeded_session.refresh(expense)

    return expense


@pytest.fixture
async def pending_confirmation_in_db(seeded_session, test_phone):
    """Create a pending confirmation in the database for testing."""
    pending = PendingConfirmation(
        user_phone=test_phone,
        data={
            "type": "expense",
            "data": {
                "description": "Test",
                "amount": 50.00,
                "category": "Alimentação",
                "payment_method": "Pix",
            },
        },
        expires_at=datetime.now() + timedelta(minutes=5),
        created_at=datetime.now(),
    )

    seeded_session.add(pending)
    await seeded_session.commit()
    await seeded_session.refresh(pending)

    return pending


@pytest.fixture
async def accepted_user_in_db(seeded_session, test_phone):
    """Create an accepted user in the database for testing."""
    user = User(
        phone=test_phone,
        accepted_terms=True,
        accepted_terms_at=datetime.now(),
        terms_version="2026-04",
        is_active=True,
        preferred_channel="whatsapp",
        timezone="America/Sao_Paulo",
        limits_enabled=True,
        daily_text_limit=100,
        daily_media_limit=20,
        daily_ai_limit=50,
        notification_preferences={"whatsapp": True},
        last_seen_at=datetime.now(),
    )
    seeded_session.add(user)
    await seeded_session.commit()
    await seeded_session.refresh(user)
    return user
