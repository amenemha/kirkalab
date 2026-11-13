"""CROFT-синхронизация пачками: большой ответ не грузится целиком, результат как раньше."""
import json

from sqlalchemy import func, select

from app.db import farm_models as fm
from app.services.farm import sync
from app.services.farm.croft import _Stream


def _dev(i, st="working"):
    return {"uniqId": f"u{i}", "type": "Antminer S21", "status": st, "hashRateMHS": 200_000_000, "factoryHashRateMHS": 200_000_000,
            "powerWH": 3500, "ip": f"10.0.0.{i % 250}", "workers": [f"acc.w{i}"], "pools": [{"url": "stratum+tcp://p:3333", "user": f"acc.w{i}", "alive": True}],
            "platform": {"uniqId": "s1", "name": "Площадка"}, "container": {"uniqId": "c1", "name": "К1"}}


def _chunks(b: bytes, n=7):
    for i in range(0, len(b), n):
        yield b[i:i + n]


def test_stream_both_shapes():
    import ijson
    data = [_dev(1), _dev(2)]
    for body in (json.dumps({"devices": data}).encode(), json.dumps(data).encode()):
        src = _Stream(_chunks(body))
        prefix = "item" if src.first() == b"[" else "devices.item"
        out = list(ijson.items(src, prefix, use_float=True))
        assert [d["uniqId"] for d in out] == ["u1", "u2"]
        assert isinstance(out[0]["hashRateMHS"], (int, float))


class _Cli:
    def __init__(self, n):
        self.n = n

    def view_groups(self):
        return [{"customer": {"uniqId": "cust"}}]

    def iter_devices(self, customer):
        for i in range(self.n):
            yield _dev(i, "working" if i % 3 else "offline")


def test_run_sync_batches(db, monkeypatch):
    monkeypatch.setattr(sync, "BATCH", 7)
    orig = db.bulk_insert_mappings
    ev, samples = [], []
    _ = orig
    # события и срезы контейнеров — BigInteger id, в SQLite не вставляются; перехватываем
    monkeypatch.setattr(db, "bulk_insert_mappings",
                        lambda m, rows: (ev if m is fm.FarmDeviceEvent else samples).extend(rows))
    n, _ = sync._apply_stream(db, _Cli(30).iter_devices("cust"))
    assert n == 30 and sum(1 for e in ev if e["kind"] == "seen") == 30
    assert db.scalar(select(func.count()).select_from(fm.FarmDevice)) == 30
    assert len(samples) == 1 and samples[0]["total"] == 30 and samples[0]["offline"] == 10
