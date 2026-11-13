"""Один проход CROFT → Kirka в отдельном процессе: `python -m app.services.farm.sync_once`.

Воркер запускает синхронизацию отдельным процессом, чтобы её память освобождалась
после каждого прохода, а сбой или нехватка памяти не останавливали кошельки,
пулы и уведомления, которые работают в основном процессе.
"""
from __future__ import annotations

import logging
import sys
import time

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.farm.croft import CroftClient
from app.services.farm.sync import run_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kirka.farm.sync")


def main() -> int:
    s = get_settings()
    client = CroftClient(s.croft_base_url, s.croft_login, s.croft_password)
    t0 = time.time()
    db = SessionLocal()
    try:
        run = run_sync(db, client)
        log.info("sync ok=%s devices=%s events=%s in %.1fs err=%s", run.ok, run.devices, run.events, time.time() - t0, run.error)
        return 0 if run.ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
