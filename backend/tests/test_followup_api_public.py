# Follow-up scan settings + on-demand AI position analysis via PUBLIC URL (Bybit /api, HotForex /api/fx, Pepperstone /api/pepper)
import os
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL") or open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split()[0]
BASE = BASE.rstrip("/")
PWD = open("/app/memory/test_credentials.md").read().split("`")[1]


@pytest.fixture(scope="module")
def h():
    r = requests.post(f"{BASE}/api/auth/login", json={"password": PWD}, timeout=20)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# Settings: defaults, bounds 0-11 / 1-30, persistence (restored after)
@pytest.mark.parametrize("prefix", ["/api", "/api/fx", "/api/pepper"])
def test_followup_settings_defaults_and_bounds(h, prefix):
    r = requests.get(f"{BASE}{prefix}/settings", headers=h, timeout=20)
    assert r.status_code == 200
    orig = r.json()
    assert "followup_scans" in orig and "followup_every_min" in orig
    try:
        for bad in ({"followup_scans": -1}, {"followup_scans": 12}, {"followup_every_min": 0}, {"followup_every_min": 31}):
            assert requests.put(f"{BASE}{prefix}/settings", json=bad, headers=h, timeout=20).status_code == 422, bad
        for ok in ({"followup_scans": 0, "followup_every_min": 1}, {"followup_scans": 11, "followup_every_min": 30}):
            assert requests.put(f"{BASE}{prefix}/settings", json=ok, headers=h, timeout=20).status_code == 200
            g = requests.get(f"{BASE}{prefix}/settings", headers=h, timeout=20).json()
            assert g["followup_scans"] == ok["followup_scans"] and g["followup_every_min"] == ok["followup_every_min"]
    finally:
        requests.put(f"{BASE}{prefix}/settings", json={"followup_scans": orig["followup_scans"], "followup_every_min": orig["followup_every_min"]}, headers=h, timeout=20)


# ai-analyze error handling
@pytest.mark.parametrize("prefix", ["/api", "/api/fx"])
@pytest.mark.parametrize("pid", ["507f1f77bcf86cd799439011", "not-an-id"])
def test_ai_analyze_unknown(h, prefix, pid):
    r = requests.post(f"{BASE}{prefix}/positions/{pid}/ai-analyze", json={"act": False}, headers=h, timeout=20)
    assert r.status_code == 404
    assert "detail" in r.json()


def test_ai_analyze_requires_auth():
    r = requests.post(f"{BASE}/api/positions/507f1f77bcf86cd799439011/ai-analyze", json={"act": False}, timeout=20)
    assert r.status_code in (401, 403)


# ai-analyze opinion/act on a paper position (real LLM) · uses ETHUSDT / GBPUSD to avoid colliding with other tests
def _open(h, prefix, symbol):
    c = requests.get(f"{BASE}{prefix}/klines", params={"symbol": symbol, "tf": "1h", "limit": 50}, headers=h, timeout=30).json()["candles"]
    price, atr = c[-1]["close"], sum(x["high"] - x["low"] for x in c[-14:]) / 14
    r = requests.post(f"{BASE}{prefix}/manual-order", json={"symbol": symbol, "side": "short", "sl": price + 2 * atr, "tp": price - 4 * atr, "atr": atr, "risk_pct": 1}, headers=h, timeout=30)
    if r.status_code == 400 and ("Mercado cerrado" in r.text or "abierta" in r.text):
        pytest.skip(r.text)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("prefix,symbol", [("/api", "ETHUSDT"), ("/api/fx", "GBPUSD")])
def test_ai_analyze_opinion_then_act(h, prefix, symbol):
    pos = _open(h, prefix, symbol)
    try:
        r = requests.post(f"{BASE}{prefix}/positions/{pos['id']}/ai-analyze", json={"act": False}, headers=h, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["action"] in ("MANTENER", "BREAKEVEN", "CERRAR", "AJUSTAR")
        assert isinstance(d["confidence"], (int, float)) and isinstance(d["reasoning"], str) and d["reasoning"]
        assert d["acted"] is None and d["mode"] == "manual_opinion" and d["id"] == pos["id"]
        p = next(x for x in requests.get(f"{BASE}{prefix}/positions", headers=h, timeout=20).json() if x["id"] == pos["id"])
        assert p["sl"] == pos["sl"] and p["tp"] == pos["tp"]
        r = requests.post(f"{BASE}{prefix}/positions/{pos['id']}/ai-analyze", json={"act": True}, headers=h, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "manual_act" and isinstance(d["acted"], str) and d["acted"]
        print(prefix, symbol, d["action"], d.get("sl"), d.get("tp"), "->", d["acted"])
        plist = requests.get(f"{BASE}{prefix}/positions", headers=h, timeout=20).json()
        still = [x for x in plist if x["id"] == pos["id"]]
        if d["action"] == "CERRAR":
            assert not still
        elif d["action"] == "AJUSTAR" and "→" in d["acted"]:
            assert (still[0]["sl"], still[0]["tp"]) != (pos["sl"], pos["tp"])
        elif d["action"] == "MANTENER":
            assert still and still[0]["sl"] == pos["sl"] and still[0]["tp"] == pos["tp"]
    finally:
        requests.post(f"{BASE}{prefix}/positions/{pos['id']}/close", headers=h, timeout=30)
