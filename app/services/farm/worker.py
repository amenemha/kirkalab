"""Background loop: `python -m app.services.farm.worker`."""
from __future__ import annotations

import logging
import threading
import time

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.farm.croft import CroftClient
from app.services.farm.sync import run_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kirka.farm.worker")


def pools_loop(every: int) -> None:
    from app.services.pools.sync import run_pools

    plog = logging.getLogger("kirka.pools.worker")
    n = 0
    while True:
        t0 = time.time()
        db = SessionLocal()
        try:
            ok, bad = run_pools(db, with_workers=True, with_profit=(n % 6 == 0))
            plog.info("pools ok=%s bad=%s in %.1fs", ok, bad, time.time() - t0)
        except Exception:
            plog.exception("pools loop failed")
        finally:
            db.close()
        n += 1
        time.sleep(max(30, every - (time.time() - t0)))


def main() -> None:
    s = get_settings()
    threading.Thread(target=pools_loop, args=(s.pools_sync_seconds,), daemon=True).start()
    if not (s.croft_login and s.croft_password):
        log.warning("CROFT_LOGIN/CROFT_PASSWORD not set — farm sync idle")
        while True:
            time.sleep(3600)
    client = CroftClient(s.croft_base_url, s.croft_login, s.croft_password)
    while True:
        t0 = time.time()
        db = SessionLocal()
        try:
            run = run_sync(db, client)
            log.info("sync ok=%s devices=%s events=%s in %.1fs err=%s", run.ok, run.devices, run.events, time.time() - t0, run.error)
        finally:
            db.close()
        time.sleep(max(5, s.croft_sync_seconds - (time.time() - t0)))


if __name__ == "__main__":
    main()
