"""Manual partial close (all bots) and REAL Bybit merge of a manual order into the open position (fake broker)."""
import asyncio
import requests
import pytest
from types import SimpleNamespace
from bson import ObjectId

import manual_ops
from models import Position

API = "http://localhost:8001"


@pytest.fixture(scope="module")
def h():
    t = requests.post(f"{API}/api/auth/login", json={"password": "BlackCat2233@"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def _positions(h, prefix):
    if prefix == "/api/lab":
        return requests.get(f"{API}{prefix}/dashboard", headers=h).json()["positions"]
    return requests.get(f"{API}{prefix}/positions", headers=h).json()


def _open(h, prefix, symbol, tp=True):
    c = requests.get(f"{API}{prefix}/klines", params={"symbol": symbol, "tf": "1h", "limit": 50}, headers=h).json()["candles"]
    price, atr = c[-1]["close"], sum(x["high"] - x["low"] for x in c[-14:]) / 14
    body = {"symbol": symbol, "side": "long", "sl": price - 2 * atr, "atr": atr, "risk_pct": 2, **({"tp": price + 4 * atr} if tp else {})}
    r = requests.post(f"{API}{prefix}/manual-order", json=body, headers=h)
    if r.status_code == 400 and "Mercado cerrado" in r.text:
        pytest.skip("market closed")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("prefix,symbol,tp", [("/api", "BTCUSDT", True), ("/api/fx", "EURUSD", True), ("/api/pepper", "EURUSD", False), ("/api/lab", "ETHUSDT", True)])
def test_partial_close_all_bots(h, prefix, symbol, tp):
    if prefix == "/api/lab" and requests.get(f"{API}{prefix}/settings", headers=h).status_code != 200:
        pytest.skip("no lab")
    pos = _open(h, prefix, symbol, tp)
    try:
        r = requests.post(f"{API}{prefix}/positions/{pos['id']}/close-partial", json={"pct": 50}, headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["closed_qty"] > 0 and abs(d["closed_qty"] + d["rest_qty"] - pos["qty"]) < 1e-6
        p = next(x for x in _positions(h, prefix) if x["id"] == pos["id"])
        assert abs(p["qty"] - d["rest_qty"]) < 1e-9 and p["initial_qty"] == pos["qty"] and p["sl"] == pos["sl"]
        assert requests.post(f"{API}{prefix}/positions/{pos['id']}/close-partial", json={"pct": 100}, headers=h).status_code == 422
    finally:
        requests.post(f"{API}{prefix}/positions/{pos['id']}/close", headers=h)


def test_partial_unknown(h):
    assert requests.post(f"{API}/api/positions/507f1f77bcf86cd799439011/close-partial", json={"pct": 50}, headers=h).status_code == 404


# ---- REAL Bybit merge with a fake broker / db
class _Col:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, q):
        return dict(self.doc) if self.doc else None

    async def update_one(self, q, u):
        self.doc.update(u["$set"])


class _Broker:
    def __init__(self):
        self.calls = []

    async def open(self, symbol, side, qty, sl, tp, lev):
        self.calls.append(("open", side, qty, sl, tp))
        return {"qty": qty, "entry": 110.0, "fee": 0.1, "order_id": "o2", "link_id": "l2", "filled": True}

    async def close(self, symbol, side, qty):
        self.calls.append(("close", side, qty))
        return {"exit": 105.0, "fee": 0.05}

    async def lot(self, symbol):
        return (0.01, 0.01)


def _engine(doc, qty):
    eng = SimpleNamespace(lock=asyncio.Lock(), broker=_Broker(), db=SimpleNamespace(positions=_Col(doc)), market=SimpleNamespace(tickers={"SOLUSDT": {"price": 110.0}}),
                          logs=[])

    async def live_size(*a):
        return {"qty": qty, "equity": 1000}

    async def log(kind, msg):
        eng.logs.append(msg)

    async def tg(*a, **k):
        return None
    eng.live_size, eng.log, eng.tg = live_size, log, tg
    return eng


def _doc():
    p = Position(symbol="SOLUSDT", side="long", strategy="manual", entry_price=100, qty=1.0, leverage=3, margin=33.3, sl=95, tp=120, initial_sl=95, atr=2, fees=0.1, mark_price=110, live=True)
    d = p.to_mongo()
    d["_id"] = ObjectId()
    return d


SET = SimpleNamespace(risk_per_trade=1, leverage=3)


def test_merge_same_side_adds_and_averages():
    doc = _doc()
    eng = _engine(doc, 1.0)
    pos, how = asyncio.run(manual_ops.bybit_merge_live(eng, doc, {"side": "long", "price": 110, "sl": 104, "tp": 130}, SET))
    assert how == "ampliada" and eng.broker.calls[0][0] == "open"
    assert pos.qty == 2.0 and abs(pos.entry_price - 105.0) < 1e-9 and pos.sl == 104 and pos.tp == 130 and pos.initial_sl == 104


def test_merge_opposite_smaller_reduces():
    doc = _doc()
    eng = _engine(doc, 0.4)
    pos, how = asyncio.run(manual_ops.bybit_merge_live(eng, doc, {"side": "short", "price": 110, "sl": 115, "tp": 100}, SET))
    assert how.startswith("reducida") and eng.broker.calls == [("close", "long", 0.4)]
    assert abs(pos.qty - 0.6) < 1e-9 and pos.side == "long" and pos.partial_gross > 0
