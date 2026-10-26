"""Хранение «по кругу», как на видеорегистраторе.

Пока диск свободен, храним всё. Когда занято больше HIGH, прореживаем самое старое:
минутные снимки контейнеров старше 30 дней сжимаем до одного в час, затем старые журналы
синхронизаций. Почасовые ряды асиков и история событий (Автотека) не трогаются, пока диск
не заполнен на CRITICAL.
"""
from __future__ import annotations

import logging
import shutil

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("kirka.retention")
HIGH, CRITICAL = 0.85, 0.93


def disk_share(path: str = "/") -> float:
    u = shutil.disk_usage(path)
    return u.used / u.total if u.total else 0.0


def run_retention(db: Session, share: float | None = None) -> dict:
    share = disk_share() if share is None else share
    done: dict = {"disk": round(share, 3)}
    if db.bind.dialect.name != "postgresql":
        return done
    # журнал синхронизаций нужен только для диагностики — держим 14 дней всегда
    done["sync_runs"] = db.execute(text("DELETE FROM farm_sync_runs WHERE started_at < now() - interval '14 days'")).rowcount
    if share >= HIGH:
        done["samples"] = db.execute(text("""
            DELETE FROM farm_container_samples s USING (
              SELECT id FROM (
                SELECT id, row_number() OVER (PARTITION BY container_id, date_trunc('hour', at) ORDER BY at) rn
                FROM farm_container_samples WHERE at < now() - interval '30 days') t WHERE rn > 1) x
            WHERE s.id = x.id""")).rowcount
    if share >= CRITICAL:
        done["hourly"] = db.execute(text("DELETE FROM farm_device_hourly WHERE hour < now() - interval '400 days'")).rowcount
        done["events"] = db.execute(text("DELETE FROM farm_device_events WHERE at < now() - interval '730 days'")).rowcount
    db.commit()
    if share >= HIGH:
        log.warning("retention: disk %.0f%% → %s", share * 100, done)
    return done
