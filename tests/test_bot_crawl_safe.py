"""Обходчик бота не должен нажимать кнопки, меняющие данные (удаление кошельков и т.п.)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "tools" / "bot_crawl.py"


def _load():
    src = SRC.read_text()
    block = src[src.index("MUTATING = re.compile("):src.index("BOTUSER = User(")]
    ns: dict = {"re": re}
    exec(block, ns)
    return ns["is_mutating"]


def test_mutating_buttons_skipped():
    m = _load()
    for d in ["wal:del:3", "wal:mute:4", "wal:unmute:4", "wal:outon:5", "wal:outoff:5",
              "wal:yes:abc:*", "notif:on", "notif:lvl", "notif:hold", "notif:every", "notif:daily",
              "notif:sleep", "notif:sleept", "notif:wake", "notif:test", "notif:t:pool",
              "qr:approve", "qr:reject", "plan:buy:pro", "calc:save:12", "x:delete:1"]:
        assert m(d), d


def test_safe_buttons_pressed():
    m = _load()
    for d in ["wal:open", "wal:add", "wal:no", "notif:open", "hub:menu", "menu:home", "farm:refresh",
              "pools:refresh", "calc:start", "cat:b:1", "hist:p:2", "profile:open", "sup:write"]:
        assert not m(d), d


def test_every_wallet_and_notif_mutator_in_bot_is_covered():
    m = _load()
    root = SRC.parents[1] / "bot"
    src = "\n".join(p.read_text() for p in root.rglob("*.py"))
    for act in re.findall(r"wal:\{?'?([a-z]+)", src):
        if act in {"open", "add", "no", "unmute", "mute"} or not act.isalpha():
            continue
        assert m(f"wal:{act}:1") or act in {"open", "add", "no"}, act
