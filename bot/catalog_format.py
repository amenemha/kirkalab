"""Pure formatting helpers for the ASIC catalog screens.

Kept free of aiogram/Telegram imports so the card/preset rendering can be
unit-tested in the standard backend test environment. NULL/empty passport
fields are skipped entirely — the user never sees a bare "None".
"""
from __future__ import annotations

from typing import Any


def _has(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _num(value: Any) -> str:
    """Render a numeric value without trailing zeros (e.g. 95.00 -> 95)."""
    s = str(value)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _title(model: dict) -> str:
    name = model.get("model_name", "")
    variant = model.get("variant")
    if _has(variant):
        return f"{model.get('brand', '')} {name} {variant}".strip()
    return f"{model.get('brand', '')} {name}".strip()


def format_device_card(model: dict) -> str:
    """Format a device passport card, skipping empty fields."""
    lines: list[str] = [f"🖥 <b>{_title(model)}</b>", ""]

    unit = model.get("hashrate_unit") or "TH/s"
    if _has(model.get("default_hashrate_ths")):
        lines.append(
            f"⚡ Хешрейт: {_num(model['default_hashrate_ths'])} {unit}"
        )
    if _has(model.get("algorithm")):
        lines.append(f"🧮 Алгоритм: {model['algorithm']}")
    if _has(model.get("default_power_w")):
        lines.append(f"🔌 Потребление: {_num(model['default_power_w'])} Вт")
    if _has(model.get("efficiency_j_per_th")):
        lines.append(
            f"📉 Эффективность: {_num(model['efficiency_j_per_th'])} J/TH"
        )
    if _has(model.get("cooling_type")):
        lines.append(f"❄️ Охлаждение: {model['cooling_type']}")
    if _has(model.get("release_year")):
        lines.append(f"📅 Год выпуска: {model['release_year']}")
    if _has(model.get("voltage_input")):
        lines.append(f"🔋 Напряжение: {model['voltage_input']}")
    if _has(model.get("noise_db")):
        lines.append(f"🔊 Шум: {_num(model['noise_db'])} dB")
    if _has(model.get("operating_temp")):
        lines.append(f"🌡 Рабочая температура: {model['operating_temp']}")
    if _has(model.get("dimensions_mm")):
        lines.append(f"📐 Габариты: {model['dimensions_mm']} мм")
    if _has(model.get("weight_kg")):
        lines.append(f"⚖️ Вес: {_num(model['weight_kg'])} кг")
    if _has(model.get("chip")):
        lines.append(f"🔩 Чип: {model['chip']}")
    if _has(model.get("network")):
        lines.append(f"🌐 Сеть: {model['network']}")
    if _has(model.get("max_hashrate_note")):
        lines.append(f"📝 {model['max_hashrate_note']}")
    if _has(model.get("notes")):
        lines.append(f"ℹ️ {model['notes']}")

    if _has(model.get("source_url")):
        lines.append("")
        lines.append(f"🔗 Источник: {model['source_url']}")

    return "\n".join(lines)


def format_firmware_list(model: dict, presets: list[dict]) -> str:
    """Format the firmware-preset list with hashrate/power deltas vs stock."""
    base_hr = _to_float(model.get("default_hashrate_ths"))
    base_pw = _to_float(model.get("default_power_w"))

    lines: list[str] = [
        "🔧 <b>Кастомные прошивки</b>",
        _title(model),
        "",
    ]
    for p in presets:
        unit = p.get("hashrate_unit") or "TH/s"
        hr = _to_float(p.get("hashrate"))
        pw = _to_float(p.get("power_w"))
        header = f"• <b>{p.get('firmware', '')}</b> — {p.get('preset_name', '')}"
        mode = p.get("mode")
        if _has(mode):
            header += f" ({mode})"
        lines.append(header)
        detail = f"   {_num(p.get('hashrate'))} {unit}"
        if base_hr is not None and hr is not None:
            detail += f" ({_delta(hr - base_hr)} {unit})"
        detail += f", {_num(p.get('power_w'))} Вт"
        if base_pw is not None and pw is not None:
            detail += f" ({_delta(pw - base_pw)} Вт)"
        lines.append(detail)
        if _has(p.get("efficiency_j_per_th")):
            lines.append(
                f"   эффективность: {_num(p['efficiency_j_per_th'])} J/TH"
            )
    return "\n".join(lines)


def _to_float(value: Any) -> float | None:
    if not _has(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(value: float) -> str:
    """Signed delta, trimmed (e.g. +12, -180, 0)."""
    sign = "+" if value > 0 else ""
    return f"{sign}{_num(round(value, 2))}"
