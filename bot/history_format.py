"""Pure formatting + pagination for the "Мои отчёты / История" screen.

Free of aiogram/Telegram imports so the whole screen (list item, list card,
detail card, empty state, pagination math) can be unit-tested in the backend
test environment without aiogram — mirroring ``bot.calc_format`` /
``bot.catalog_format``.

The bot's history handler fetches the page from the API (which already applies
the retention filter server-side) and hands the resulting item dicts here for
rendering. Each item dict carries the snapshot stored at calc time:

    {
      "id": int,
      "device_name": str | None,
      "quantity": int,
      "currency": str,
      "net_profit_day_usdt": str | None,
      "net_profit_month_usdt": str | None,
      "hashrate_ths": str | None,
      "power_w": int | None,
      "power_price": str | None,
      "created_at": str (ISO-8601),
    }
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Items shown per page on the history list. Kept small so the inline keyboard
# (one button per item + a pagination row + a "back" row) stays well under
# Telegram's limits and the live screen reads cleanly.
PAGE_SIZE = 5

_CURRENCY_SYMBOL = {
    "USDT": "USDT",
    "USD": "$",
    "RUB": "₽",
    "CNY": "¥",
    "EUR": "€",
    "KZT": "₸",
}

_DEFAULT_DEVICE_NAME = "Оборудование"


def _sym(currency: str) -> str:
    return _CURRENCY_SYMBOL.get((currency or "USDT").upper(), currency)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(value: Any, currency: str = "USDT", places: int = 2) -> str:
    d = _dec(value)
    if d is None:
        return "—"
    q = Decimal(10) ** -places
    d = d.quantize(q)
    whole, _, frac = f"{d:.{places}f}".partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    grouped = f"{int(whole):,}".replace(",", " ")
    body = f"{sign}{grouped}.{frac}" if places else f"{sign}{grouped}"
    return f"{body} {_sym(currency)}"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_datetime(value: Any) -> str:
    """Render an ISO timestamp as ``DD.MM.YYYY HH:MM`` (graceful fallback)."""
    dt = _parse_dt(value)
    if dt is None:
        return str(value or "—")
    return dt.strftime("%d.%m.%Y %H:%M")


def _device_name(item: dict) -> str:
    return (item.get("device_name") or _DEFAULT_DEVICE_NAME).strip() or _DEFAULT_DEVICE_NAME


# --------------------------------------------------------------------------- #
# Pagination math (pure).
# --------------------------------------------------------------------------- #
def page_count(total: int, page_size: int = PAGE_SIZE) -> int:
    """Number of pages for ``total`` items (at least 1, even when empty)."""
    if total <= 0:
        return 1
    size = max(page_size, 1)
    return (total + size - 1) // size


def clamp_page(page: int, total: int, page_size: int = PAGE_SIZE) -> int:
    """Coerce ``page`` into ``[0, last_page]``."""
    last = page_count(total, page_size) - 1
    if page < 0:
        return 0
    if page > last:
        return last
    return page


def page_offset(page: int, page_size: int = PAGE_SIZE) -> int:
    return max(page, 0) * max(page_size, 1)


# --------------------------------------------------------------------------- #
# List item + list screen.
# --------------------------------------------------------------------------- #
def format_list_item(item: dict, *, index: int | None = None) -> str:
    """One line for the list card: date/time + device ×qty + daily profit.

    ``index`` (1-based) is prepended when given so the text lines up with the
    numbered open-buttons under the screen."""
    when = format_datetime(item.get("created_at"))
    name = _device_name(item)
    qty = int(item.get("quantity") or 1)
    currency = item.get("currency") or "USDT"
    profit = _money(item.get("net_profit_day_usdt"), currency)
    prefix = f"{index}. " if index is not None else ""
    return f"{prefix}🗓 {when}\n   {name} × {qty} · {profit}/день"


def format_list_screen(
    items: list[dict],
    *,
    page: int,
    total: int,
    is_pro: bool,
    truncated: bool = False,
    retention_days: int = 0,
    page_size: int = PAGE_SIZE,
) -> str:
    """Full list card text for the current page.

    ``truncated`` is True when the FREE retention window hid older rows, so the
    soft PRO hint about extended history is appended (never on PRO)."""
    pages = page_count(total, page_size)
    page = clamp_page(page, total, page_size)

    lines: list[str] = ["📊 <b>Мои отчёты</b>", ""]
    start = page_offset(page, page_size)
    for offset, item in enumerate(items, start=1):
        lines.append(format_list_item(item, index=start + offset))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()

    if pages > 1:
        lines.append("")
        lines.append(f"Страница {page + 1} из {pages}")

    if truncated and not is_pro:
        lines.append("")
        lines.append(_retention_hint(retention_days))

    return "\n".join(lines)


def _retention_hint(retention_days: int) -> str:
    if retention_days > 0:
        window = f"последние {retention_days} дн."
        return (
            f"🔒 На бесплатном тарифе доступны {window} истории. "
            "Полная история — в PRO 💎"
        )
    return "🔒 Расширенная история расчётов — в PRO 💎"


def format_empty_screen() -> str:
    """Friendly empty state (no saved calcs, or all hidden by retention)."""
    return (
        "📊 <b>Мои отчёты</b>\n\n"
        "У вас пока нет сохранённых расчётов.\n"
        "Сделайте первый расчёт доходности — он сохранится здесь автоматически."
    )


# --------------------------------------------------------------------------- #
# Detail screen.
# --------------------------------------------------------------------------- #
def format_detail_screen(item: dict) -> str:
    """Full detail card for one saved calculation (params + headline result)."""
    name = _device_name(item)
    qty = int(item.get("quantity") or 1)
    currency = item.get("currency") or "USDT"
    when = format_datetime(item.get("created_at"))

    lines: list[str] = [f"📊 <b>{name}</b> × {qty}", f"🗓 {when}", ""]

    lines.append("⚙️ <b>Параметры</b>")
    hashrate = item.get("hashrate_ths")
    if hashrate is not None:
        lines.append(f"  • хешрейт: {_trim(hashrate)} TH/s")
    power = item.get("power_w")
    if power is not None:
        lines.append(f"  • потребление: {power} Вт")
    lines.append(f"  • количество: {qty}")
    price = item.get("power_price")
    if price is not None:
        lines.append(
            f"  • цена э/э: {_money(price, 'USDT', places=4)}/кВт·ч"
        )
    lines.append("")

    lines.append("✅ <b>Чистая прибыль</b>")
    lines.append(
        f"  • в день: {_money(item.get('net_profit_day_usdt'), currency)}"
    )
    month = item.get("net_profit_month_usdt")
    if month is not None:
        lines.append(f"  • в месяц: {_money(month, currency)}")

    return "\n".join(lines)


def _trim(value: Any) -> str:
    d = _dec(value)
    if d is None:
        return str(value)
    text = f"{d:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
