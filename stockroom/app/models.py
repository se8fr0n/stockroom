from datetime import datetime, timezone
from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def now():
    return datetime.now(timezone.utc)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(100), default="General", index=True)
    price_kes: Mapped[int] = mapped_column(Integer)  # whole shillings, no float errors
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    movements: Mapped[list["StockMovement"]] = relationship(back_populates="item", cascade="all, delete-orphan")

    @property
    def status(self) -> str:
        if self.quantity <= 0:
            return "sold_out"
        if self.quantity <= self.reorder_level:
            return "low"
        return "in_stock"


class StockMovement(Base):
    """Every stock change is logged: this is the audit trail."""
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    change: Mapped[int] = mapped_column(Integer)  # +restock, -sale
    reason: Mapped[str] = mapped_column(String(50))  # sale, restock, damaged, correction, initial
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    item: Mapped[Item] = relationship(back_populates="movements")
