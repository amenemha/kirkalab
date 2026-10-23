"""Kirka error classes (ТЗ гл. 20) mapped from CROFT alarm classes and codes."""
from __future__ import annotations

# Kirka class key -> (label RU, order)
CLASSES: dict[str, tuple[str, int]] = {
    "offline": ("Нет связи", 0),
    "power": ("Питание", 1),
    "temperature": ("Температура", 2),
    "hashrate": ("Хешрейт", 3),
    "hardware": ("Аппаратные", 4),
    "network": ("Сеть и пул", 5),
    "config": ("Конфигурация", 6),
    "pressure": ("Давление гидро", 7),
    "other": ("Прочее", 8),
}

_CROFT = {
    "power": "power", "temperature": "temperature", "hash rate": "hashrate",
    "hardware": "hardware", "network": "network", "configuration": "config",
}


def classify(alarm: dict) -> str:
    code = str(alarm.get("code") or "")
    msg = str(alarm.get("message") or "").lower()
    if code == "N1000" or "offline" in msg:
        return "offline"
    if "pressure" in msg or "давлен" in msg:
        return "pressure"
    if "power" in msg and "fan" not in msg:
        return "power"
    return _CROFT.get(str(alarm.get("classText") or "").lower(), "other")


LEVELS = {0: "info", 1: "warning", 2: "danger", 3: "critical"}
