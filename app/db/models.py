from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    handle: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Bumped whenever every issued refresh token for this user must be
    # invalidated at once (e.g. on password change). The value is embedded in
    # refresh tokens and compared on use.
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QrLoginSession(Base):
    __tablename__ = "qr_login_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    # pending | approved | consumed | expired
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeviceModel(Base):
    """Catalog of mining hardware (internal ASIC database)."""

    __tablename__ = "device_models"
    __table_args__ = (
        UniqueConstraint("brand", "model_name", name="uq_device_models_brand_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    brand: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String, default="SHA-256", nullable=False)
    coin_family: Mapped[str] = mapped_column(String, default="BTC", nullable=False)
    default_hashrate_ths: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    default_power_w: Mapped[int] = mapped_column(Integer, nullable=False)
    released_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # verified | normalized | factory
    data_quality: Mapped[str] = mapped_column(
        String, default="factory", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DeviceProfile(Base):
    """A concrete hardware configuration: either a system preset or a
    user-defined custom profile (premium feature)."""

    __tablename__ = "device_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    base_model_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("device_models.id", ondelete="SET NULL"), nullable=True
    )
    # system | custom
    profile_type: Mapped[str] = mapped_column(
        String, default="system", nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    hashrate_ths: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    power_w: Mapped[int] = mapped_column(Integer, nullable=False)
    cooling_type: Mapped[str | None] = mapped_column(String, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MarketSnapshot(Base):
    """Point-in-time snapshot of external market data used by the calc core.

    Snapshots double as the durable fallback cache: the most recent valid row
    is reused when the upstream APIs are unavailable."""

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    coin_code: Mapped[str] = mapped_column(
        String, default="BTC", nullable=False, index=True
    )
    network_difficulty: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    block_reward_btc: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    price_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RevokedToken(Base):
    """Blacklist of refresh-token ``jti`` values that may no longer be used.

    A row is written when a refresh token is rotated (so the old one cannot be
    replayed) or otherwise revoked. Rows are pruned once ``expires_at`` has
    passed, since an expired JWT is rejected on its own merits anyway.
    """

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    jti: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
