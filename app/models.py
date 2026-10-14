from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PARENT = "parent"
KIDS = "kids"

# Types d'entrées
HABIT = "habit"
PENALTY = "penalty"
BONUS = "bonus"
REWARD = "reward"

# Statuts
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(10))
    password_hash: Mapped[str] = mapped_column(String(255))

    @property
    def is_parent(self) -> bool:
        return self.role == PARENT


class Child(Base):
    __tablename__ = "children"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(20), default="#a78bfa")
    # Solde de départ (report des fiches papier)
    starting_balance: Mapped[int] = mapped_column(Integer, default=0)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # Un enfant retiré est masqué mais son historique est conservé.
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppSetting(Base):
    """Réglages généraux clé/valeur (ex. nom de la famille)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class _CatalogItem:
    """Champs communs aux habitudes, pénalités et récompenses.

    `points` est toujours positif : gain (habitude), retrait (pénalité) ou coût (récompense).
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    points: Mapped[int] = mapped_column(Integer, default=1)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Habit(_CatalogItem, Base):
    __tablename__ = "habits"


class Rule(_CatalogItem, Base):
    __tablename__ = "rules"

    max_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Reward(_CatalogItem, Base):
    __tablename__ = "rewards"


class Entry(Base):
    """Un mouvement de points pour un enfant à une date donnée."""

    __tablename__ = "entries"
    __table_args__ = (UniqueConstraint("child_id", "day", "habit_id", name="uq_habit_per_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10))
    habit_id: Mapped[int | None] = mapped_column(ForeignKey("habits.id"), nullable=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    reward_id: Mapped[int | None] = mapped_column(ForeignKey("rewards.id"), nullable=True)
    # Libellé et points figés à la saisie : l'historique ne bouge pas si on modifie les règles.
    label: Mapped[str] = mapped_column(String(120))
    points: Mapped[int] = mapped_column(Integer)  # signé : + gain, - retrait / dépense
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=PENDING, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    validated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    child: Mapped[Child] = relationship()
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    validated_by: Mapped[User | None] = relationship(foreign_keys=[validated_by_id])
