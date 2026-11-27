"""1h follow-up scans (Bybit, HotForex) + on-demand AI analysis of one open position."""
import asyncio
import time
import requests
import pytest
import pandas as pd
from types import SimpleNamespace

import followup
import ai_funnel
import market
from fx.engine import FxEngine  # noqa: F401  (import check)

API = "http://localhost:8001"
PWD = "BlackCat2233@"
S = lambda **k: SimpleNamespace(**{"timeframe": "1h", "followup_scans": 5, "followup_every_min": 5, "max_positions": 3, **k})  # noqa: E731


@pytest.fixture(scope="module")
def h():
    t = requests.post(f"{API}/api/auth/login", json={"password": PWD}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def test_times_default_schedule():
    base = 1_800_000_000 - 1_800_000_000 % 3600
    ts = followup.times(base + 120, 5, 5, 5)
    assert [int(t - base) for t in ts] == [305, 605, 905, 1205, 1505]  # :05:05 … :25:05


def test_times_never_past_hour():
    base = 1_800_000_000 - 1_800_000_000 % 3600
    assert len(followup.times(base, 5, 11, 10)) == 5  # 10,20,30,40,50 → 60 dropped


def test_pending_and_next():
    base = 1_800_000_000 - 1_800_000_000 % 3600
    s = S()
    assert followup.pending(base + 200, 5, s, 0) is None
    k = followup.pending(base + 306, 5, s, 0)
    assert k[:2] == (1, 5) and k[2] == base + 305
    assert followup.pending(base + 306, 5, s, base + 305) is None  # already done
    assert followup.pending(base + 305 + 300, 5, s, base + 305)[:2] == (2, 5)
    assert followup.next_ts(base + 1600, 5, s) is None  # after :25:05 → wait for the next close
    assert followup.next_ts(base + 10, 5, s) == base + 305


def test_enabled_rules():
    eng = SimpleNamespace(followups=True)
    assert followup.enabled(eng, S())
    assert not followup.enabled(eng, S(timeframe="15m"))
    assert not followup.enabled(eng, S(followup_scans=0))
    assert not followup.enabled(SimpleNamespace(followups=True, candle_only=True), S())
    assert not followup.enabled(SimpleNamespace(followups=False), S())


def _df(o, h, lo, c, atr=10.0, start=None):
    start = start or int(time.time() // 3600 * 3600 * 1000)
    return pd.DataFrame([{"ts": start - 3600_000, "open": 100, "high": 110, "low": 95, "close": 105, "atr": atr},
                         {"ts": start, "open": o, "high": h, "low": lo, "close": c, "atr": atr}])


def test_live_candle_rejection_and_label():
    start = int(time.time() // 3600 * 3600 * 1000)
    lc = followup.live_candle(_df(100, 112, 99, 101, start=start), price=101, now=start / 1000 + 12 * 60 + 5)
    assert lc["label"] == "vela 1h en curso (minuto 13/60)"
    assert "rechazo bajista" in lc["rejection"] and lc["upper_wick_pct"] >= 50
    line = followup.prompt_line(lc)
    assert "VELA 1H EN CURSO (MINUTO 13/60)" in line and "NO es una vela cerrada" in line
    assert followup.prompt_line(None) == ""


def test_live_candle_uses_live_price():
    lc = followup.live_candle(_df(100, 101, 99, 100), price=120)
    assert lc["high"] == 120 and lc["price"] == 120 and lc["direction"] == "alcista"


def test_rules_prompt_contains_live_candle(monkeypatch):
    seen = {}

    async def fake_ask(chat, prompt):
        seen["p"] = prompt
        return '{"violates": false, "rule": 0, "reason": "ok"}'
    monkeypatch.setattr(ai_funnel, "_ask", fake_ask)
    monkeypatch.setattr(ai_funnel, "_chat", lambda *a, **k: SimpleNamespace())
    sig = {"side": "long", "strategy": "trend_ema", "price": 100, "sl": 95, "tp": 110, "score": 3, "reasons": ["x"], "indicators": {}}
    lc = followup.live_candle(_df(100, 112, 99, 101), price=101)
    asyncio.run(ai_funnel.rules_check("BTCUSDT", "1h", sig, {"live_candle": lc}, [{"text": "regla", "id": "1"}]))
    assert "VELA 1H EN CURSO" in seen["p"]


def test_loop_imports_followup():
    assert hasattr(market, "followup")


@pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
def test_settings_roundtrip(h, prefix):
    r = requests.get(f"{API}{prefix}/settings", headers=h).json()
    assert r["followup_scans"] == 5 and r["followup_every_min"] == 5
    assert requests.put(f"{API}{prefix}/settings", json={"followup_scans": 3, "followup_every_min": 10}, headers=h).status_code == 200
    r = requests.get(f"{API}{prefix}/settings", headers=h).json()
    assert r["followup_scans"] == 3 and r["followup_every_min"] == 10
    assert requests.put(f"{API}{prefix}/settings", json={"followup_scans": 12}, headers=h).status_code == 422
    requests.put(f"{API}{prefix}/settings", json={"followup_scans": 5, "followup_every_min": 5}, headers=h)


def test_analyze_unknown_position(h):
    assert requests.post(f"{API}/api/positions/507f1f77bcf86cd799439011/ai-analyze", json={"act": False}, headers=h).status_code == 404


def _open(h, prefix, symbol):
    c = requests.get(f"{API}{prefix}/klines", params={"symbol": symbol, "tf": "1h", "limit": 50}, headers=h).json()["candles"]
    price, atr = c[-1]["close"], sum(x["high"] - x["low"] for x in c[-14:]) / 14
    r = requests.post(f"{API}{prefix}/manual-order", json={"symbol": symbol, "side": "long", "sl": price - 2 * atr, "tp": price + 4 * atr, "atr": atr, "risk_pct": 1}, headers=h)
    if r.status_code == 400 and "Mercado cerrado" in r.text:
        pytest.skip("market closed")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("prefix,symbol", [("/api", "SOLUSDT"), ("/api/fx", "USDJPY")])
def test_analyze_opinion_and_act(h, prefix, symbol):
    pos = _open(h, prefix, symbol)
    try:
        r = requests.post(f"{API}{prefix}/positions/{pos['id']}/ai-analyze", json={"act": False}, headers=h, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["action"] in ("MANTENER", "BREAKEVEN", "CERRAR", "AJUSTAR") and d["acted"] is None and d["mode"] == "manual_opinion"
        p = next(x for x in requests.get(f"{API}{prefix}/positions", headers=h).json() if x["id"] == pos["id"])
        assert p["sl"] == pos["sl"] and p["tp"] == pos["tp"] and p["ai_review"]["mode"] == "manual_opinion"
        r = requests.post(f"{API}{prefix}/positions/{pos['id']}/ai-analyze", json={"act": True}, headers=h, timeout=120)
        assert r.status_code == 200, r.text
        assert r.json()["acted"] and r.json()["mode"] == "manual_act"
    finally:
        requests.post(f"{API}{prefix}/positions/{pos['id']}/close", headers=h)


class _Col:
    def __init__(self):
        self.sets = []

    async def update_one(self, q, u):
        self.sets.append(u["$set"])


def _pos(**k):
    from models import Position
    base = dict(id="507f1f77bcf86cd799439011", symbol="BTCUSDT", side="long", strategy="manual", entry_price=100, qty=1, leverage=3, margin=10,
                sl=95, tp=110, initial_sl=95, atr=2, fees=0, mark_price=100)
    return Position(**{**base, **k})


def test_set_levels_paper_valid_and_invalid():
    import position_review
    eng = SimpleNamespace(db=SimpleNamespace(positions=_Col()))
    out = asyncio.run(position_review._set_levels(eng, _pos(), 98, 115, 103))
    assert "SL 95 → 98" in out and "TP 110 → 115" in out and eng.db.positions.sets[-1] == {"sl": 98.0, "tp": 115.0}
    out = asyncio.run(position_review._set_levels(eng, _pos(), 105, 101, 103))  # SL above price / TP below price on a long
    assert "no aplicables" in out


def test_set_levels_live_bybit_calls_broker():
    import position_review
    calls = []

    async def set_stop(*a):
        calls.append(a)
    eng = SimpleNamespace(db=SimpleNamespace(positions=_Col()), broker=SimpleNamespace(set_stop=set_stop))
    asyncio.run(position_review._set_levels(eng, _pos(live=True), 99, None, 103))
    assert calls == [("BTCUSDT", 99.0, 110)]
