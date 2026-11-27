"""Manual orders carry per-operation management overrides (Bybit, HotForex, Pepperstone)."""
import os
import requests
import pytest
from types import SimpleNamespace

import risk
from fx import risk as fx_risk
import position_review

API = "http://localhost:8001"
PWD = "BlackCat2233@"
BYBIT_M = {"breakeven_enabled": False, "be_start_r": 1.2, "trailing_enabled": True, "trail_start_r": 2.0, "trail_atr_mult": 1.5,
           "partial_tp_enabled": True, "partial_tp_r": 1.0, "partial_tp_pct": 30, "ai_review": True}
EMA_M = {"ema_be_on": True, "ema_be_atr": 0.8, "ema_trail_on": False, "ema_trail_atr": 2.5, "ema_partial_on": True,
         "ema_partial_at_atr": 1.2, "ema_partial_pct": 40, "ai_review": False}


@pytest.fixture(scope="module")
def h():
    t = requests.post(f"{API}/api/auth/login", json={"password": PWD}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def _last(h, prefix, symbol):
    r = requests.get(f"{API}{prefix}/klines", params={"symbol": symbol, "tf": "1h", "limit": 50}, headers=h)
    assert r.status_code == 200, r.text
    c = r.json()["candles"]
    atr = sum(x["high"] - x["low"] for x in c[-14:]) / 14
    return c[-1]["close"], atr


def _open(h, prefix, symbol, manage, tp=True):
    price, atr = _last(h, prefix, symbol)
    body = {"symbol": symbol, "side": "long", "sl": price - 3 * atr, "atr": atr, "risk_pct": 1, "manage": manage}
    if tp:
        body["tp"] = price + 6 * atr
    return requests.post(f"{API}{prefix}/manual-order", json=body, headers=h)


def _close(h, prefix, pid):
    requests.post(f"{API}{prefix}/positions/{pid}/close", headers=h)


def test_pos_rules_merge():
    base = risk.trade_rules({})
    p = SimpleNamespace(manage=BYBIT_M)
    m = risk.pos_rules(base, p)
    assert m["breakeven_enabled"] is False and m["partial_tp_pct"] == 30 and m["trail_atr_mult"] == 1.5
    assert risk.pos_rules(base, SimpleNamespace(manage=None)) is base
    assert fx_risk.pos_rules(fx_risk.trade_rules({}), SimpleNamespace(manage=EMA_M))["ema_be_atr"] == 0.8


def test_ai_wanted():
    on, off = SimpleNamespace(ai_position_review_enabled=True), SimpleNamespace(ai_position_review_enabled=False)
    assert position_review._wanted({"manage": {"ai_review": False}}, on) is False
    assert position_review._wanted({"manage": {"ai_review": True}}, off) is True
    assert position_review._wanted({}, on) is True and position_review._wanted({}, off) is False


def test_invalid_manage_rejected(h):
    bad = {**BYBIT_M, "partial_tp_pct": 90}
    assert _open(h, "/api", "BTCUSDT", bad).status_code == 422


@pytest.mark.parametrize("prefix,symbol,manage,tp", [("/api", "BTCUSDT", BYBIT_M, True), ("/api/fx", "EURUSD", BYBIT_M, True),
                                                     ("/api/pepper", "EURUSD", EMA_M, False)])
def test_manual_order_persists_manage(h, prefix, symbol, manage, tp):
    if symbol is None:
        symbol = requests.get(f"{API}{prefix}/settings", headers=h).json()["symbols"][0]
    r = _open(h, prefix, symbol, manage, tp)
    if r.status_code == 400 and "Mercado cerrado" in r.text:
        pytest.skip("market closed")
    assert r.status_code == 200, r.text
    pos = r.json()
    try:
        assert pos["manage"] == manage
        listed = requests.get(f"{API}{prefix}/positions", headers=h).json()
        rows = listed if isinstance(listed, list) else listed.get("positions", [])
        assert any(x.get("id") == pos["id"] and x.get("manage") == manage for x in rows)
    finally:
        _close(h, prefix, pos["id"])
