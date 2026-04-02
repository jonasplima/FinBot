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
