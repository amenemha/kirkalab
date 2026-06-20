from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.session import Base

# JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite in tests).
JsonB = JSON().with_variant(JSONB(), "postgresql")


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
    # PRO tier flag. Gates the firmware economy delta and saving custom builds.
    # Kept as the fast entitlement check; the authoritative expiry is
    # ``premium_until`` (see ``subscriptions``). ``is_pro`` is the materialized
    # view of "premium_until is in the future"; lazy expiration flips it back to
    # False on read once the date has passed.
    is_pro: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # When the active PRO subscription lapses (UTC). NULL = never been PRO /
    # no current entitlement. The single source of truth for entitlement timing.
    premium_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Bumped whenever every issued refresh token for this user must be
    # invalidated at once (e.g. on password change). The value is embedded in
    # refresh tokens and compared on use.
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Plan(Base):
    """A billable tariff. ``code`` is the stable key used everywhere else.

    The FREE tier is seeded as a row too (price 0, no period) so the catalog is
    a single source of truth, but only the PRO plans are sent as invoices. Prices
    are stored here, never hardcoded in the bot/API (CALC_SPEC §4): Telegram
    Stars are whole-number amounts, so ``price_stars`` is a plain Integer (no
    NUMERIC — nothing for PostgreSQL precision to enforce)."""

    __tablename__ = "plans"

    # 'free' | 'pro_monthly' | 'pro_yearly'
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Length of the entitlement granted; NULL for the perpetual FREE plan.
    period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Telegram Stars are integer amounts (XTR). 0 for the FREE plan.
    price_stars: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(8), default="XTR", server_default="XTR", nullable=False
    )
    # PRO entitlements/limits as config (CALC_SPEC §5), e.g. unlimited_calcs.
    features_json: Mapped[Any | None] = mapped_column(JsonB, nullable=True)
    limits_json: Mapped[Any | None] = mapped_column(JsonB, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Subscription(Base):
    """One PRO purchase/renewal, paid with Telegram Stars.

    Idempotency is enforced structurally: ``telegram_payment_charge_id`` is
    UNIQUE, so re-delivering the same ``successful_payment`` (Telegram retries)
    can never create a duplicate renewal. The activate flow looks the charge id
    up first and returns the existing row unchanged on a repeat.

    ``price_stars``/``total_amount`` are integer Stars (XTR) — no NUMERIC."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("plans.code"), nullable=False
    )
    # active | expired | refunded
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Telegram's charge id for the Stars payment; the idempotency key.
    telegram_payment_charge_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    # Stars actually paid (XTR), echoed for audit/refund.
    total_amount: Mapped[int] = mapped_column(
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
        UniqueConstraint(
            "brand",
            "model_name",
            "variant",
            name="uq_device_models_brand_model_variant",
        ),
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

    # --- Passport card: full spec sheet (all nullable) ---
    series: Mapped[str | None] = mapped_column(String, nullable=True)
    variant: Mapped[str | None] = mapped_column(String, nullable=True)
    hashrate_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    efficiency_j_per_th: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    cooling_type: Mapped[str | None] = mapped_column(String, nullable=True)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voltage_input: Mapped[str | None] = mapped_column(String, nullable=True)
    noise_db: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    operating_temp: Mapped[str | None] = mapped_column(String, nullable=True)
    dimensions_mm: Mapped[str | None] = mapped_column(String, nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    chip: Mapped[str | None] = mapped_column(String, nullable=True)
    network: Mapped[str | None] = mapped_column(String, nullable=True)
    max_hashrate_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class FirmwarePreset(Base):
    """System-wide firmware tuning presets for a device model.

    A base ``device_models`` row carries factory hashrate/power. A custom
    firmware (Vnish/Braiins/LuxOS/Pitbit/stock) shifts those: overclock raises
    TH/s and watts, undervolt/underclock lowers watts. Each row is one such
    operating point, shared across all users (``is_system=True``)."""

    __tablename__ = "firmware_presets"
    __table_args__ = (
        UniqueConstraint(
            "device_model_id",
            "firmware",
            "preset_name",
            name="uq_firmware_presets_model_fw_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("device_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # vnish / braiins / luxos / pitbit / stock
    firmware: Mapped[str] = mapped_column(String, nullable=False)
    preset_name: Mapped[str] = mapped_column(String, nullable=False)
    # overclock / underclock / undervolt / balanced / stock
    mode: Mapped[str] = mapped_column(String, nullable=False)
    hashrate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    hashrate_unit: Mapped[str] = mapped_column(
        String, default="TH/s", server_default="TH/s", nullable=False
    )
    power_w: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    efficiency_j_per_th: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserFirmwareBuild(Base):
    """A user's own saved firmware build (PRO feature).

    Unlike :class:`FirmwarePreset` these are free-form labels the user attaches
    to a tuning of their own (e.g. "Vnish разгон", "Pitbit даунвольт с деффи").
    Saving builds is a PRO capability; the gating is enforced at the
    service/endpoint layer (``User.is_pro``)."""

    __tablename__ = "user_firmware_builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("device_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    build_name: Mapped[str] = mapped_column(String, nullable=False)
    firmware: Mapped[str | None] = mapped_column(String, nullable=True)
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    hashrate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    hashrate_unit: Mapped[str] = mapped_column(
        String, default="TH/s", server_default="TH/s", nullable=False
    )
    power_w: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CalculationRun(Base):
    """One profitability calculation performed by a user.

    Rows are the audit/counter backing the FREE funnel: the number of rows a
    user has, plus how many fall on the current UTC day, drives the intro/daily
    limits and the currency-blur stages (see ``app.services.calc.funnel``).

    Money is ``Decimal``; specs use modest precision/scale so PostgreSQL's
    NUMERIC enforcement never overflows (the SQLite test DB does not enforce,
    so the bounds must be correct by construction)."""

    __tablename__ = "calculation_runs"
    __table_args__ = (
        Index("ix_calculation_runs_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_model_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("device_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Echo of the inputs used (for history / "recalculate").
    hashrate_ths: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    power_w: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    power_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), default="USDT", server_default="USDT", nullable=False
    )
    # Snapshot of the headline result, USDT (Decimal money).
    net_profit_day_usdt: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ManualImportFile(Base):
    """An uploaded Excel/CSV file queued for manual earnings import (Queue 4).

    Neutral groundwork: the row records the upload and its parse status; the
    parsing/normalization logic (into ``pool_earnings`` with source
    ``'manual_xlsx'``) is a later queue. Only schema + FK here, no logic. No
    NUMERIC columns, so there is nothing for PostgreSQL precision to enforce."""

    __tablename__ = "manual_import_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    # pending | parsed | failed
    status: Mapped[str] = mapped_column(
        String, default="pending", server_default="pending", nullable=False
    )
    rows_parsed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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


class UserSettings(Base):
    """Per-user preferences. One row per user (free tier).

    PRO-only behaviour (multiple price profiles, saved devices, language
    switching) is intentionally out of scope here — this table holds the single
    set of settings available to every user.
    """

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    # RU/EN/KZ/UK; NULL = follow Telegram locale
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Saved price per kWh (available to all users, free tier included)
    default_power_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    currency: Mapped[str | None] = mapped_column(
        String(8), default="USDT", server_default="USDT", nullable=True
    )
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    hide_small_assets: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
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


# ---------------------------------------------------------------------------
# Queue 3 groundwork: neutral schema for the RU tax module + mining-pool
# integration. These tables carry only PK/FK and technical columns; every
# logical field is nullable because the business logic (pool parsers, wallet
# scanning, FX-rate lookups, report generation) is not implemented yet.
# ---------------------------------------------------------------------------


class PoolConnection(Base):
    """Read-only link to a user's mining-pool account (observer access)."""

    __tablename__ = "pool_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # viabtc / f2pool / antpool / binance / luxor
    pool_code: Mapped[str] = mapped_column(String, nullable=False)
    observer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored encrypted once the integration lands; plaintext never persisted.
    access_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    coin: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PoolEarning(Base):
    """Normalized daily earnings pulled from a pool connection."""

    __tablename__ = "pool_earnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pool_connection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pool_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    coin: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_crypto: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    source: Mapped[str] = mapped_column(
        String, default="pool", server_default="pool", nullable=False
    )
    raw_json: Mapped[Any | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WalletSource(Base):
    """On-chain wallet a user wants included in the RU tax report."""

    __tablename__ = "wallet_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WalletEarning(Base):
    """Incoming on-chain transaction credited to a wallet source."""

    __tablename__ = "wallet_earnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    wallet_source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("wallet_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tx_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    coin: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_crypto: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    raw_json: Mapped[Any | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaxRate(Base):
    """FX/asset rate on the crediting date, used to value crypto income."""

    __tablename__ = "tax_rates"
    __table_args__ = (
        Index(
            "ix_tax_rates_date_coin_currency_source",
            "date",
            "coin",
            "currency",
            "source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    coin: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    # cbr / coingecko
    source: Mapped[str | None] = mapped_column(String, nullable=True)


class TaxReport(Base):
    """A generated tax report for a user over a period."""

    __tablename__ = "tax_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # month / year
    period_type: Mapped[str | None] = mapped_column(String, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    jurisdiction: Mapped[str] = mapped_column(
        String, default="RU", server_default="RU", nullable=False
    )
    # draft / generated
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    totals_json: Mapped[Any | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaxDeduction(Base):
    """A deductible expense applied against taxable mining income."""

    __tablename__ = "tax_deductions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_report_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tax_reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # electricity / amortization / rent
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
