"""SQLAlchemy models for FinBot."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .connection import Base


class Category(Base):
    """Expense/Income category."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    type = Column(String(10), nullable=False)  # "Positivo" or "Negativo"

    # Relationships
    expenses = relationship("Expense", back_populates="category")
    budgets = relationship("Budget", back_populates="category")

    def __repr__(self) -> str:
        return f"<Category(id={self.id}, name='{self.name}', type='{self.type}')>"


class PaymentMethod(Base):
    """Payment method."""

    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)

    # Relationships
    expenses = relationship("Expense", back_populates="payment_method")

    def __repr__(self) -> str:
        return f"<PaymentMethod(id={self.id}, name='{self.name}')>"


class Expense(Base):
    """Expense or income record."""

    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)

    # Foreign keys
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"), nullable=False)

    # Type (inherited from category, but stored for quick access)
    type = Column(String(10), nullable=False)  # "Positivo" or "Negativo"

    # Installments
    installment_current = Column(Integer, nullable=True)
    installment_total = Column(Integer, nullable=True)

    # Shared expense
    is_shared = Column(Boolean, default=False, nullable=False)
    shared_percentage = Column(Numeric(5, 2), nullable=True)

    # Currency conversion (stores original currency/amount if not BRL)
    original_currency = Column(String(3), nullable=True)  # ISO code: USD, EUR, etc.
    original_amount = Column(Numeric(12, 2), nullable=True)
    exchange_rate = Column(Numeric(12, 6), nullable=True)  # Rate used for conversion

    # Recurring expense
    is_recurring = Column(Boolean, default=False, nullable=False)
    recurring_day = Column(Integer, nullable=True)
    recurring_active = Column(Boolean, default=True, nullable=True)

    # Dates
    date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())

    # Relationships
    category = relationship("Category", back_populates="expenses")
    payment_method = relationship("PaymentMethod", back_populates="expenses")

    # Indexes
    __table_args__ = (
        Index("ix_expenses_user_date", "user_phone", "date"),
        Index("ix_expenses_recurring", "is_recurring", "recurring_active"),
    )

    def __repr__(self) -> str:
        return f"<Expense(id={self.id}, description='{self.description}', amount={self.amount})>"

    @property
    def installment_display(self) -> str | None:
        """Get installment display string."""
        if self.installment_current and self.installment_total:
            return f"{self.installment_current}/{self.installment_total}"
        return None


class PendingConfirmation(Base):
    """Pending expense confirmation waiting for user response."""

    __tablename__ = "pending_confirmations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    data = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())

    def __repr__(self) -> str:
        return f"<PendingConfirmation(id={self.id}, user_phone='{self.user_phone}')>"

    @property
    def is_expired(self) -> bool:
        """Check if confirmation has expired."""
        return datetime.now() > self.expires_at


class Budget(Base):
    """Monthly budget limit per category."""

    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    monthly_limit = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.now)

    # Relationships
    category = relationship("Category", back_populates="budgets")
    alerts = relationship("BudgetAlert", back_populates="budget", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (Index("ix_budgets_user_category", "user_phone", "category_id"),)

    def __repr__(self) -> str:
        return f"<Budget(id={self.id}, user_phone='{self.user_phone}', limit={self.monthly_limit})>"


class BudgetAlert(Base):
    """Track sent budget alerts to avoid duplicates."""

    __tablename__ = "budget_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey("budgets.id"), nullable=False)
    threshold_percent = Column(Integer, nullable=False)  # 50, 80, or 100
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    sent_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())

    # Relationships
    budget = relationship("Budget", back_populates="alerts")

    # Indexes to quickly check if alert was already sent
    __table_args__ = (
        Index(
            "ix_budget_alerts_unique",
            "budget_id",
            "threshold_percent",
            "month",
            "year",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<BudgetAlert(budget_id={self.budget_id}, threshold={self.threshold_percent}%, {self.month}/{self.year})>"


class Goal(Base):
    """Savings goal for a user."""

    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_phone = Column(String(20), nullable=False, index=True)
    description = Column(String(200), nullable=False)
    target_amount = Column(Numeric(12, 2), nullable=False)
    current_amount = Column(Numeric(12, 2), default=0, nullable=False)  # Manual deposits
    deadline = Column(Date, nullable=False)
    start_date = Column(Date, nullable=False, default=date.today)
    is_active = Column(Boolean, default=True, nullable=False)
    is_achieved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.now)

    # Relationships
    updates = relationship("GoalUpdate", back_populates="goal", cascade="all, delete-orphan")

    # Indexes for query performance
    __table_args__ = (
        Index("ix_goals_user_active", "user_phone", "is_active"),
        Index("ix_goals_deadline", "deadline"),
    )

    def __repr__(self) -> str:
        return (
            f"<Goal(id={self.id}, description='{self.description}', target={self.target_amount})>"
        )


class GoalUpdate(Base):
    """Track goal progress updates for history and motivation tracking."""

    __tablename__ = "goal_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    previous_amount = Column(Numeric(12, 2), nullable=False)
    new_amount = Column(Numeric(12, 2), nullable=False)
    update_type = Column(String(20), nullable=False)  # "automatic", "manual", "deposit"
    created_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())

    # Relationships
    goal = relationship("Goal", back_populates="updates")

    def __repr__(self) -> str:
        return f"<GoalUpdate(goal_id={self.goal_id}, type='{self.update_type}', amount={self.new_amount})>"


class ExchangeRate(Base):
    """Cached exchange rates for currency conversion fallback."""

    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    currency_code = Column(String(3), nullable=False, unique=True)  # ISO code: USD, EUR, etc.
    rate_to_brl = Column(Numeric(12, 6), nullable=False)
    source = Column(String(30), nullable=False)  # "wise", "exchangerate_api", "manual"
    updated_at = Column(DateTime, nullable=False, default=datetime.now, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ExchangeRate(currency={self.currency_code}, rate={self.rate_to_brl}, source='{self.source}')>"
